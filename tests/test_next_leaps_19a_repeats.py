"""19-a (build-out plan R3.23, idea 19): THE THIRD TIME, A SKILL DRAFT FROM
YOUR CHATS. The repeat detector, ``learning/repeats``.

Pinned here, each with the way it would fail:

* the rule (``groups``, pure): three chat tasks on three different days with
  the same request shape and two tools in the same order make ONE group;
  two make none; three on ONE day make none; a low-trust task breaks the
  group; a task in another client's folder never joins; the same words with
  nothing in common in the work make none; the same file kinds in and out
  are enough without shared tools; a task from a chat another computer or
  channel writes never counts; five weeks is the window;
* ``groups`` reads only its input (the same answer twice, no database);
* ``units`` reads the chats from the record: the owner's own request words,
  the tools in order from the LEDGER (a tool row of another chat's turn at
  the same moment is not this chat's), the file kinds (extensions only),
  the model that answered, Local only; and it is BOUNDED (the newest
  ``THREADS_MAX`` chats in the window, nothing older);
* ``detect`` adds ONE chat candidate per repeat and never a second for the
  same tasks; a repeat a skill, a template or a schedule already covers is
  skipped; one like a chat draft the owner rejected is skipped; skill
  learning switched off adds nothing; client names that cannot be read add
  nothing (fail closed).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentType, ChatThreadRecord, SavedPromptRecord, ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.learning import repeats as R
from iron_jarvis.skills.learning_models import SkillCandidateRecord, SkillProposalRecord

WORDS = "turn the bank export into a summary sheet with totals per category"
ALIKE = "turn this bank export into a summary sheet with the totals per category"
TOOLS = ("excel_read", "excel_edit")


def _monday(weeks_back: int, hour: int = 10) -> datetime:
    """A Monday ``weeks_back`` weeks before the most recent Monday that is
    at least a day ago, at ``hour`` local time, aware."""
    today = datetime.now().astimezone()
    last = today - timedelta(days=(today.weekday() or 7))
    day = last - timedelta(weeks=weeks_back)
    return day.replace(hour=hour, minute=0, second=0, microsecond=0)


def U(start: datetime, *, words: str = WORDS, tools=TOOLS, kin=("csv",), kout=("xlsx",), owner: str = "",
      low: bool = False, remote: bool = False, ref: str = "") -> R.Unit:
    return R.Unit(source="chat", ref=ref or f"chat_{start.timestamp():.0f}", start=start.timestamp(),
                  words=words, tools=tuple(tools), kinds_in=frozenset(kin), kinds_out=frozenset(kout),
                  provider="claude-cli", model="claude-sonnet-4-6", owner=owner, low_trust=low, remote=remote)


THREE = [U(_monday(0)), U(_monday(1), words=ALIKE), U(_monday(2))]


# --------------------------------------------------------------------------- #
# the rule (pure)
# --------------------------------------------------------------------------- #
def test_three_chats_on_three_mondays_make_one_group():
    got = R.groups(THREE)
    assert len(got) == 1
    group = got[0]
    assert [u.start for u in group.units] == sorted((u.start for u in THREE), reverse=True)
    assert group.newest.start == THREE[0].start
    assert group.tools == list(TOOLS)
    assert group.kinds_in == ["csv"] and group.kinds_out == ["xlsx"]
    assert group.days == sorted(R.day_of(u.start).isoformat() for u in THREE)


def test_two_make_none():
    assert R.groups(THREE[:2]) == []


def test_three_on_one_day_make_none():
    day = _monday(0)
    assert R.groups([U(day), U(day + timedelta(hours=2)), U(day + timedelta(hours=4))]) == []


def test_three_times_on_two_days_make_none():
    assert R.groups([U(_monday(0)), U(_monday(0, hour=15)), U(_monday(1))]) == []


def test_a_low_trust_task_breaks_the_group():
    assert R.groups([THREE[0], THREE[1], U(_monday(2), low=True)]) == []


def test_a_chat_another_computer_writes_never_counts():
    assert R.groups([THREE[0], THREE[1], U(_monday(2), remote=True)]) == []


def test_a_task_in_another_client_s_folder_never_joins():
    a = [U(_monday(w), owner="client:wc_a") for w in range(3)]
    assert len(R.groups(a)) == 1
    mixed = [U(_monday(0), owner="client:wc_a"), U(_monday(1), owner="client:wc_b"), U(_monday(2))]
    assert R.groups(mixed) == []


def test_a_task_whose_owner_is_unknown_never_joins():
    assert R.groups([THREE[0], THREE[1], U(_monday(2), owner=R.UNKNOWN_OWNER)]) == []


def test_the_same_words_with_nothing_in_common_in_the_work_make_none():
    other = [U(_monday(w), tools=(t,), kin=(), kout=()) for w, t in enumerate(("web_fetch", "excel_read", "shell"))]
    assert R.groups(other) == []


def test_the_same_file_kinds_in_and_out_are_enough_without_shared_tools():
    units = [U(_monday(0), tools=("excel_read",)), U(_monday(1), tools=("read_file",)), U(_monday(2), tools=())]
    (group,) = R.groups(units)
    assert group.tools == [] and group.kinds_out == ["xlsx"]


def test_different_requests_make_none():
    units = [THREE[0], THREE[1], U(_monday(2), words="draft a polite reply to the landlord about the repairs")]
    assert R.groups(units) == []


def test_two_tools_must_share_their_order():
    units = [U(_monday(0), tools=("excel_read", "excel_edit"), kin=(), kout=()),
             U(_monday(1), tools=("excel_edit", "excel_read"), kin=(), kout=()),
             U(_monday(2), tools=("excel_read", "excel_edit"), kin=(), kout=())]
    assert R.groups(units) == []


def test_five_weeks_is_the_window():
    assert R.groups([U(_monday(0)), U(_monday(1)), U(_monday(0) - timedelta(days=36))]) == []
    assert len(R.groups([U(_monday(0)), U(_monday(1)), U(_monday(0) - timedelta(days=34))])) == 1


def test_the_rule_reads_only_its_input():
    first = R.groups(list(THREE))
    second = R.groups(list(reversed(THREE)))
    assert [[u.start for u in g.units] for g in first] == [[u.start for u in g.units] for g in second]


def test_the_lines_say_the_days_and_the_tools_from_the_record():
    evidence = R.evidence_of(R.groups(THREE)[0])
    assert R.times_line({**evidence, "days": ["2026-11-03", "2026-11-10", "2026-11-17"]}) == (
        "You asked for this 3 times, on 3, 10 and 17 November."
    )
    assert R.days_words(["2026-10-27", "2026-11-03", "2026-11-10"]) == "27 October, 3 and 10 November"
    assert R.tools_line(evidence) == "Each time Iron Jarvis used: excel read, excel edit."
    assert R.tools_line({**evidence, "tools": []}) == ""
    # never a file's name in what is kept
    assert "bank-export" not in json.dumps(evidence)


# --------------------------------------------------------------------------- #
# reading the chats (the record)
# --------------------------------------------------------------------------- #
@pytest.fixture
def platform(tmp_path):
    app = create_app(str(tmp_path / "home"))
    yield app.state.platform


def _utc(at: datetime) -> datetime:
    return at.astimezone(timezone.utc).replace(tzinfo=None)


def seed_chat(engine, at: datetime, *, words: str = WORDS, tools=TOOLS, provider: str = "claude-cli",
              model: str = "claude-sonnet-4-6", trust: str | None = None, setup: dict | None = None,
              owner: str = "user", files=("C:/Work/bank-export.csv", "C:/Work/summary.xlsx")) -> str:
    """One saved chat with one finished task at ``at``: the owner's request,
    the reply, the turn's run row and its tool rows in the ledger."""
    reply = {"role": "assistant", "content": "Done: the summary sheet is written.",
             "at": (at + timedelta(seconds=60)).isoformat()}
    if trust:
        reply["trust"] = trust
    messages = [{"role": "user", "content": words, "at": at.isoformat()}, reply]
    with session_scope(engine) as db:
        t = ChatThreadRecord(title="bank", messages_json=json.dumps(messages), owner=owner,
                             setup_json=json.dumps(setup) if setup else "")
        t.created_at = _utc(at)
        t.updated_at = _utc(at + timedelta(seconds=61))
        db.add(t)
        run = AgentRun(session_id="chat", agent_type=AgentType.BUILDER, provider=provider, model=model,
                       thread_id=t.id)
        run.created_at = _utc(at + timedelta(seconds=1))
        run.finished_at = _utc(at + timedelta(seconds=55))
        db.add(run)
        for k, tool in enumerate(tools):
            row = ToolInvocation(session_id="chat", agent_run_id="chat", tool=tool, ok=True, output="ok",
                                 args_json=json.dumps({"path": files[min(k, len(files) - 1)]}))
            row.created_at = _utc(at + timedelta(seconds=10 + k))
            db.add(row)
        db.commit()
        return t.id


def test_units_come_from_the_record(platform):
    engine = platform.engine
    tid = seed_chat(engine, _monday(0), setup={"local_only": True})
    (unit,) = R.units(engine, _monday(0) - timedelta(days=1))
    assert unit.ref == tid and unit.words == WORDS
    assert unit.tools == TOOLS
    assert unit.kinds_in == {"csv"} and unit.kinds_out == {"xlsx"}
    assert (unit.provider, unit.model) == ("claude-cli", "claude-sonnet-4-6")
    assert unit.local_only is True and unit.low_trust is False


def test_a_tool_row_of_another_chat_s_turn_is_not_this_chat_s(platform):
    """Two chats' turns overlap: each tool row belongs to the turn whose
    window holds it, the latest-starting first (the ledger's own rule)."""
    engine = platform.engine
    at = _monday(0)
    mine = seed_chat(engine, at, tools=("excel_read",))
    # another chat whose turn starts 15 seconds later (after my excel_read at +10 s)
    # and uses shell at +20 s, inside both windows: the later-starting turn has it
    with session_scope(engine) as db:
        t = ChatThreadRecord(title="other", messages_json=json.dumps(
            [{"role": "user", "content": "list the downloads folder please now", "at": (at + timedelta(seconds=15)).isoformat()},
             {"role": "assistant", "content": "ok", "at": (at + timedelta(seconds=40)).isoformat()}]))
        t.updated_at = _utc(at + timedelta(seconds=41))
        db.add(t)
        run = AgentRun(session_id="chat", agent_type=AgentType.BUILDER, provider="claude-cli", model="x",
                       thread_id=t.id)
        run.created_at = _utc(at + timedelta(seconds=15))
        run.finished_at = _utc(at + timedelta(seconds=30))
        db.add(run)
        row = ToolInvocation(session_id="chat", agent_run_id="chat", tool="shell", ok=True, output="",
                             args_json="{}")
        row.created_at = _utc(at + timedelta(seconds=20))
        db.add(row)
        db.commit()
        other = t.id
    got = {u.ref: u for u in R.units(engine, at - timedelta(days=1))}
    assert got[mine].tools == ("excel_read",)
    assert got[other].tools == ("shell",)


def test_a_low_trust_reply_and_a_channel_s_chat_are_marked(platform):
    engine = platform.engine
    seed_chat(engine, _monday(0), trust="low")
    seed_chat(engine, _monday(1), owner="daemon")
    got = sorted(R.units(engine, _monday(3)), key=lambda u: -u.start)
    assert [u.low_trust for u in got] == [True, False]
    assert [u.remote for u in got] == [False, True]


def test_the_read_is_bounded(platform, monkeypatch):
    engine = platform.engine
    ids = [seed_chat(engine, _monday(w)) for w in range(4)]
    old = seed_chat(engine, _monday(0) - timedelta(days=60))
    since = _monday(0) - timedelta(days=R.WINDOW_DAYS)
    assert {u.ref for u in R.units(engine, since)} == set(ids)  # nothing older than the window
    monkeypatch.setattr(R, "THREADS_MAX", 2)
    assert {u.ref for u in R.units(engine, since)} == set(ids[:2])  # the newest chats only
    assert old not in {u.ref for u in R.units(engine, since - timedelta(days=60))}


# --------------------------------------------------------------------------- #
# detect: candidates
# --------------------------------------------------------------------------- #
def _cands(engine) -> list[SkillCandidateRecord]:
    with session_scope(engine) as db:
        return list(db.exec(select(SkillCandidateRecord)))


def _three(engine, **kw) -> list[str]:
    return [seed_chat(engine, _monday(w), words=ALIKE if w == 1 else WORDS, **kw) for w in range(3)]


def test_three_chats_leave_one_chat_candidate_and_never_two(platform):
    engine = platform.engine
    tids = _three(engine)
    assert R.detect(platform) == 1
    (cand,) = _cands(engine)
    assert cand.source == "chat" and cand.status == "pending" and cand.session_id == tids[0]
    ev = json.loads(cand.evidence_json)
    assert [u["thread"] for u in ev["units"]] == tids
    assert ev["tools"] == list(TOOLS) and ev["kinds_in"] == ["csv"] and ev["kinds_out"] == ["xlsx"]
    assert len(ev["days"]) == 3
    assert R.detect(platform) == 0 and len(_cands(engine)) == 1
    # a fourth time later still holds the same tasks: no second candidate
    seed_chat(engine, _monday(0) + timedelta(days=1))
    assert R.detect(platform) == 0


def test_a_repeat_a_skill_already_covers_is_skipped(platform, monkeypatch):
    _three(platform.engine)
    monkeypatch.setattr(type(platform.skill_learning), "_matches_existing_skill", lambda self, task: True)
    assert R.detect(platform) == 0 and _cands(platform.engine) == []


def test_a_repeat_a_template_or_a_schedule_covers_is_skipped(platform):
    from iron_jarvis.scheduling.models import ScheduledTaskRecord

    engine = platform.engine
    _three(engine)
    with session_scope(engine) as db:
        db.add(SavedPromptRecord(name="bank", task=WORDS))
        db.commit()
    assert R.detect(platform) == 0
    with session_scope(engine) as db:
        for row in db.exec(select(SavedPromptRecord)):
            db.delete(row)
        db.add(ScheduledTaskRecord(name="bank weekly", kind="task", payload_json=json.dumps({"task": ALIKE})))
        db.commit()
    assert R.detect(platform) == 0
    with session_scope(engine) as db:
        for row in db.exec(select(ScheduledTaskRecord)):
            db.delete(row)
        db.commit()
    assert R.detect(platform) == 1


def test_a_repeat_like_a_rejected_chat_draft_is_skipped(platform):
    engine = platform.engine
    _three(engine)
    # the rejected draft keeps no words; its candidate (same signature) does
    with session_scope(engine) as db:
        db.add(SkillCandidateRecord(session_id="chat_x", task=ALIKE, signature="chat::earlier", source="chat",
                                    status="distilled", evidence_json=json.dumps(
                                        {"units": [{"thread": "chat_x", "start": 1.0, "words": ALIKE}]})))
        db.add(SkillProposalRecord(kind="create", skill_name="bank", source="chat", status="rejected",
                                   signature="chat::earlier", evidence_json=json.dumps(
                                       {"units": [{"thread": "chat_x", "start": 1.0}]})))
        db.commit()
    assert R.detect(platform) == 0


def test_skill_learning_off_adds_nothing(platform):
    _three(platform.engine)
    platform.config.skill_learning_enabled = False
    assert R.detect(platform) == 0


def test_client_names_that_cannot_be_read_add_nothing(platform, monkeypatch):
    from iron_jarvis.workmap.clients import store

    _three(platform.engine)

    def broken(engine):
        raise store.ClientsUnreadable()

    monkeypatch.setattr(store, "load_map", broken)
    assert R.detect(platform) == 0


def test_a_chat_in_a_client_s_folder_does_not_group_with_others(platform):
    from iron_jarvis.workmap.clients import store
    from iron_jarvis.workmap.models import CLIENT_CONFIRMED, WorkClient, WorkClientFolder

    from iron_jarvis.workmap import keys
    from iron_jarvis.workmap.clients import names

    engine = platform.engine
    folder = "C:/Clients/Hartleys"
    with session_scope(engine) as db:
        db.add(WorkClient(id="wc_hart", name="Hartleys", signature=names.signature("Hartleys"),
                          status=CLIENT_CONFIRMED))
        db.add(WorkClientFolder(client_id="wc_hart", folder=folder,
                                folder_key=keys.folder_key(keys.home_folder(folder))))
        db.commit()
    store.bump_version()
    seed_chat(engine, _monday(0), files=("C:/Clients/Hartleys/bank-export.csv", "C:/Clients/Hartleys/sum.xlsx"))
    seed_chat(engine, _monday(1))
    seed_chat(engine, _monday(2))
    assert R.detect(platform) == 0


# --------------------------------------------------------------------------- #
# review fixes
# --------------------------------------------------------------------------- #
def test_the_rule_looks_at_the_newest_units_only(monkeypatch):
    """Bounded: only the newest UNITS_MAX units are grouped. Three matching
    units just past the bound make nothing; inside it they group."""
    base = _monday(0)
    filler = [U(base - timedelta(minutes=k), words=f"unrelated request number {k} about gardening",
                tools=("shell",), kin=(), kout=()) for k in range(R.UNITS_MAX)]
    old = [U(_monday(w) - timedelta(hours=1)) for w in range(1, 4)]
    assert R.groups(filler + old) == []
    monkeypatch.setattr(R, "UNITS_MAX", R.UNITS_MAX + 3)
    assert len(R.groups(filler + old)) == 1


def test_detect_reads_what_it_checks_against_once(platform, monkeypatch):
    for words in (WORDS, "write the weekly status email to the whole team with the open items"):
        for w in range(3):
            seed_chat(platform.engine, _monday(w, hour=9 if words == WORDS else 14), words=words,
                      tools=TOOLS if words == WORDS else ("read_document", "write_document"),
                      files=("C:/Work/a.csv", "C:/Work/b.xlsx") if words == WORDS else ("C:/W/n.docx", "C:/W/o.docx"))
    calls = []
    real = R._known
    monkeypatch.setattr(R, "_known", lambda engine: calls.append(1) or real(engine))
    assert R.detect(platform) == 2
    assert calls == [1]


def test_the_same_request_in_one_chat_on_three_mondays_is_three_times(platform):
    """A chat reopened every Monday with the same line: the task splitter
    calls it a retry; here each re-send on a new day after a good reply is a
    new piece of work."""
    engine = platform.engine
    lines = []
    for w in (2, 1, 0):
        at = _monday(w)
        lines += [{"role": "user", "content": WORDS, "at": at.isoformat()},
                  {"role": "assistant", "content": "Done: the summary sheet is written.",
                   "at": (at + timedelta(seconds=60)).isoformat()}]
    with session_scope(engine) as db:
        t = ChatThreadRecord(title="bank", messages_json=json.dumps(lines))
        t.updated_at = _utc(_monday(0) + timedelta(seconds=61))
        db.add(t)
        for w in (2, 1, 0):
            at = _monday(w)
            run = AgentRun(session_id="chat", agent_type=AgentType.BUILDER, provider="claude-cli",
                           model="claude-sonnet-4-6", thread_id=t.id)
            run.created_at, run.finished_at = _utc(at + timedelta(seconds=1)), _utc(at + timedelta(seconds=55))
            db.add(run)
            for k, tool in enumerate(TOOLS):
                row = ToolInvocation(session_id="chat", agent_run_id="chat", tool=tool, ok=True, output="ok",
                                     args_json=json.dumps({"path": ("C:/W/x.csv", "C:/W/y.xlsx")[k]}))
                row.created_at = _utc(at + timedelta(seconds=10 + k))
                db.add(row)
        db.commit()
    us = R.units(engine, _monday(3))
    assert len(us) == 3 and all(u.tools == TOOLS for u in us)
    (group,) = R.groups(us)
    assert len(group.days) == 3
