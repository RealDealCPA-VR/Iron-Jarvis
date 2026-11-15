"""The Improvements inbox (v1.495.0, vision plan leap 5, Phase 1).

ONE read over every suggestion that waits for the user's answer. Every test
here seeds the REAL stores of a scratch install and reads ``GET
/improvements``; decisions are pressed through the address the inbox itself
handed out, against the real routes.
"""

from __future__ import annotations

import importlib
import inspect
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.capability.models import CapabilityProposalRecord
from iron_jarvis.capability.store import CapabilityProposalStore
from iron_jarvis.coach.models import CoachProposalRecord
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.ids import new_id
from iron_jarvis.core.models import EventRecord
from iron_jarvis.core.models import Session as RunRow
from iron_jarvis.daemon.app import create_app
from iron_jarvis.goals.engine import _goal_origin
from iron_jarvis.goals.models import GoalContractRecord
from iron_jarvis.harness.library_models import HarnessLibraryRecord
from iron_jarvis.learning import preferences as prefs
from iron_jarvis.learning.models import LessonRecord
from iron_jarvis.memory.proposals import MemoryProposalRecord, MemoryProposalStore
from iron_jarvis.motivation.models import ProposalRecord
from iron_jarvis.skills.learning_models import SkillProposalRecord
from iron_jarvis.workmap.models import WorkClient, WorkClientFolder

# The package re-exports the function ``collect``, which shadows the module.
C = importlib.import_module("iron_jarvis.inbox.collect")
G = importlib.import_module("iron_jarvis.daemon.routes.goals")

NOTHING = {"items": [], "groups": [], "total": 0, "unreadable": []}


@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    s = SimpleNamespace(app=app, client=TestClient(app), platform=app.state.platform, tmp=tmp_path)
    s.engine = s.platform.engine
    # The two stores that make their own table on first use.
    MemoryProposalStore(s.engine, ltm=getattr(s.platform, "ltm", None), home=s.platform.config.home)
    CapabilityProposalStore(s.engine, platform=s.platform)

    def failing(**kw):
        raise AssertionError("the inbox called a model")

    s.platform.router.complete = failing
    return s


def _add(desk, row):
    with session_scope(desk.engine) as db:
        db.add(row)
        db.commit()
        return row.id


def _inbox(desk) -> dict:
    res = desk.client.get("/improvements")
    assert res.status_code == 200, res.text
    return res.json()


def _only(desk, kind: str) -> dict:
    got = [i for i in _inbox(desk)["items"] if i["kind"] == kind]
    assert len(got) == 1, [i["id"] for i in _inbox(desk)["items"]]
    return got[0]


def _status(desk, model, rid):
    with session_scope(desk.engine) as db:
        return db.get(model, rid).status


# ---- seeds ------------------------------------------------------------------
def seed_preference(desk, text="Keep answers short", status="proposed", **more):
    return _add(desk, LessonRecord(
        text=text, source="preference", origin="noticed", status=status, weight=5, scope="user",
        evidence_json=json.dumps([{"quote": "shorter please", "at": "", "where": "chat"},
                                  {"quote": "too long again", "at": "", "where": "phone"}]),
        signature=f"sig-{text}", **more,
    ))


def seed_skill(desk, name="month-end-close", kind="create", status="pending", body="# Month end\n\nSteps.", **more):
    more.setdefault("source_session_ids", json.dumps(["session_a", "session_b"]))
    return _add(desk, SkillProposalRecord(
        kind=kind, skill_name=name, description="Close the books at month end", body_md=body,
        prev_body_md="# Month end\n\nOld." if kind == "refine" else "",
        signature=f"sig-{name}", status=status, **more,
    ))


def seed_coach(desk, status="pending", agent="tax-reader", before="Read the return."):
    return _add(desk, CoachProposalRecord(
        agent=f"custom:{agent}", kind="instructions", target=agent,
        before=before, after=f"{before} Always name the form.",
        rationale="It skipped the form name in 3 runs.",
        evidence_json=json.dumps([{"category": "human-correction", "count": 3, "evidence": [], "weight": 3}]),
        signature=f"sig-coach-{agent}-{status}", status=status,
    ))


def seed_habit(desk, agent="glm-worker"):
    return _add(desk, CoachProposalRecord(
        agent=f"custom:{agent}", kind="team_note", target=agent, before="",
        after="Run the tests before saying a change is done.",
        rationale="From 4 confirmed findings across 2 sessions",
        evidence_json=json.dumps([{"id": "f1", "path": "cart.py", "line": 12, "what": "untested change"}]),
        signature="sig-habit", status="pending",
    ))


def seed_lesson(desk, folder="C:/work/books", status="proposed", text="Use the ledger helper, not raw SQL."):
    return _add(desk, HarnessLibraryRecord(
        folder_key=folder.lower(), folder=folder, kind="lesson", text=text, status=status,
        evidence_json=json.dumps({"paths": ["ledger.py"], "quotes": ["use the helper"], "stripped": 0, "turns": 1}),
        signature=f"sig-{text}", thread_id="chat_abc", turn_id="t1",
    ))


def seed_session_edit(desk, folder="C:/work/books"):
    return _add(desk, HarnessLibraryRecord(
        folder_key=folder.lower(), folder=folder, kind="layer_edit", text="Say how to run the tests.",
        file="memory.md", before="Old notes.\n", after="Old notes.\nRun pytest -q.\n", status="proposed",
        evidence_json=json.dumps({"paths": [], "quotes": [], "stripped": 0, "turns": 1}),
        signature="sig-edit", thread_id="chat_abc", turn_id="t2",
    ))


def seed_memory(desk, status="pending", base="notes", survivor="clients/hartleys.md",
                remove=("clients/hartleys-2.md",), says="Keep the first note and remove the second.",
                text="Hartleys, 1 High St."):
    return _add(desk, MemoryProposalRecord(
        kind="duplicate", base=base, refs=json.dumps([survivor, *remove]),
        rationale="Both notes hold the same address.", suggested_action=says,
        payload_json=json.dumps({"survivor_ref": survivor, "text": text, "remove_refs": list(remove)}),
        signature=f"sig-mem-{survivor}-{status}", status=status,
    ))


def seed_tool(desk, kind="tool", name="count-lines", status="pending"):
    return _add(desk, CapabilityProposalRecord(
        kind=kind, name=name, name_norm=name, rationale="Counting lines by hand is slow.",
        scope="Read files in the working folder.", task="tidy the books",
        spec_json=json.dumps({"command": ["wc", "-l", "{path}"],
                              "parameters": [{"name": "path", "type": "string", "required": True}],
                              "timeout_seconds": 10}),
        signature=f"{kind}::{name}", status=status,
    ))


def seed_wants(desk, status="pending", agent_type="builder", title="Tidy the downloads folder"):
    return _add(desk, ProposalRecord(
        title=title, rationale="A watcher saw 40 new files.",
        action_json=json.dumps({"agent_type": agent_type, "task": "Sort the new files in Downloads by client."}),
        risk="low", source="sentinel", status=status,
    ))


def seed_goal(desk, name="Watch the tax rates", **more):
    return _add(desk, GoalContractRecord(name=name, contract_text="Report any change in the rate table.", **more))


def seed_asks(desk, gid, tool="web_fetch", n=3, decision="once", args_hash=""):
    """``n`` approval asks by one run of that goal, each answered ``decision``."""
    sid = _add(desk, RunRow(task="one iteration", origin=_goal_origin(gid)))
    with session_scope(desk.engine) as db:
        for _ in range(n):
            aid = new_id("apr")
            asked = {"tool": tool, "approval_id": aid}
            if args_hash:
                asked.update(args_hash=args_hash, args={"url": "https://example.com/rates"})
            db.add(EventRecord(id=new_id("evt"), type=EventType.APPROVAL_REQUESTED, session_id=sid,
                               payload_json=json.dumps(asked)))
            db.add(EventRecord(id=new_id("evt"), type=EventType.APPROVAL_RESOLVED, session_id=sid,
                               payload_json=json.dumps({"tool": tool, "decision": decision, "approval_id": aid})))
        db.commit()
    return sid


def seed_goal_with_approved_asks(desk, tool="web_fetch", n=3):
    gid = seed_goal(desk)
    seed_asks(desk, gid, tool=tool, n=n)
    return gid


def seed_client(desk, name="Hartleys", status="proposed", **more):
    """v1.499.0: a name noticed in the owner's folder names (made-up folders)."""
    three_days = (datetime.utcnow() - timedelta(days=3)).isoformat()
    homes = [
        {"folder": f"C:\\Clients\\{name}", "key": f"c:/clients/{name.lower()}", "work": 6, "last_at": three_days,
         "local": False},
        {"folder": f"D:/Scans/Clients/{name} 2024", "key": f"d:/scans/clients/{name.lower()} 2024", "work": 3,
         "last_at": "", "local": False},
    ]
    more.setdefault("evidence_json", json.dumps({"rule": "under_clients", "homes": homes, "work": 9, "folders": 2}))
    return _add(desk, WorkClient(id=new_id("client"), name=name, name_key=name.lower(), signature=f"sig-{name}",
                                 status=status, **more))


def _clients_routes(desk) -> bool:
    """Builder A's ``daemon/routes/clients.py`` (the client's own answer
    routes) is registered in this build."""
    return any(str(getattr(r, "path", "")).startswith("/clients/") for r in desk.app.routes)


def seed_everything(desk):
    return {
        "preference": seed_preference(desk), "client": seed_client(desk), "skill": seed_skill(desk),
        "coach": seed_coach(desk),
        "habit": seed_habit(desk), "lesson": seed_lesson(desk), "session_edit": seed_session_edit(desk),
        "memory": seed_memory(desk), "tool": seed_tool(desk), "wants": seed_wants(desk),
        "unasked": seed_goal_with_approved_asks(desk),
    }


def brain_notes(desk) -> tuple[str, str]:
    """Two real notes in the install's own notes folder (a base that can be
    rewritten here)."""
    conn = desk.platform.ltm.get("brain")
    one, two = Path(conn.dir) / "hartleys.md", Path(conn.dir) / "hartleys-copy.md"
    one.write_text("Hartleys, 1 High St.\n", encoding="utf-8")
    two.write_text("Hartleys, 1 High Street\n", encoding="utf-8")
    return str(one), str(two)


# ---- the read ---------------------------------------------------------------
def test_nothing_waiting_is_an_empty_inbox(desk):
    assert _inbox(desk) == NOTHING


def test_every_kind_is_listed_once_in_the_pages_order(desk):
    seed_everything(desk)
    got = _inbox(desk)
    kinds = [g["kind"] for g in got["groups"]]
    assert kinds == [k for k, _label in C.KINDS]
    assert [g["label"] for g in got["groups"]] == [label for _k, label in C.KINDS]
    assert all(g["count"] == 1 and g["shown"] == 1 for g in got["groups"])
    assert got["total"] == len(C.KINDS) and got["unreadable"] == []
    ids = [i["id"] for i in got["items"]]
    assert len(ids) == len(set(ids)) == len(C.KINDS)
    # Items come grouped in the same order as the groups.
    assert [i["kind"] for i in got["items"]] == kinds


def test_each_item_says_what_it_is_in_plain_words(desk):
    seed_everything(desk)
    by = {i["kind"]: i for i in _inbox(desk)["items"]}
    assert by["preference"]["title"] == "Keep answers short"
    assert by["preference"]["evidence"] == [
        "You said, in a chat: “shorter please”", "You said, on your phone: “too long again”"]
    assert by["client"]["title"] == "Is Hartleys a client?"
    assert by["client"]["about"] == "2 folders, 9 pieces of work"
    assert by["client"]["evidence"] == ["Clients › Hartleys: 6 pieces of work, last 3 days ago",
                                        "Clients › Hartleys 2024: 3 pieces of work"]
    assert by["client"]["why"].startswith("Iron Jarvis saw this name in your folder names.")
    assert "Nothing was sent anywhere" in by["client"]["why"]
    assert by["skill"]["title"] == "New skill: month-end-close"
    assert by["skill"]["evidence"] == ["Written from 2 earlier runs"]
    assert by["skill"]["detail"]["mode"] == "text" and "Month end" in by["skill"]["detail"]["after"]
    assert by["coach"]["title"] == "Change the instructions of tax-reader"
    assert by["coach"]["detail"] == {
        "mode": "diff", "before": "Read the return.", "after": "Read the return. Always name the form.",
        "label": "The agent's instructions", "cut": False}
    # The coach's finding in words, never its category id.
    assert by["coach"]["evidence"] == ["What the coach saw: the user had to /continue the run afterwards"]
    assert by["habit"]["title"] == "Run the tests before saying a change is done."
    assert by["habit"]["about"] == "glm-worker" and by["habit"]["evidence"] == ["cart.py:12: untested change"]
    assert by["lesson"]["title"] == "Use the ledger helper, not raw SQL." and by["lesson"]["about"] == "books"
    assert by["lesson"]["detail"] is None  # a short lesson is its own title
    assert by["session_edit"]["title"] == "Change memory.md for coding sessions in books"
    assert by["session_edit"]["detail"]["mode"] == "diff" and by["session_edit"]["detail"]["label"] == "memory.md"
    assert by["memory"]["title"] == "Two notes say the same thing in notes"
    assert by["tool"]["title"] == "A tool: count-lines"
    assert by["tool"]["detail"]["after"] == "wc -l {path}"
    assert by["wants"]["title"] == "Tidy the downloads folder"
    assert by["wants"]["evidence"] == ["Low risk", "A watcher noticed a change"]
    assert by["wants"]["detail"]["after"] == "Sort the new files in Downloads by client."
    assert by["unasked"]["title"] == "Let the goal “Watch the tax rates” use web_fetch without asking"
    assert by["unasked"]["evidence"] == ["It asked 3 times and you approved every time."]


def test_a_tool_request_names_its_kind_in_the_apps_words(desk):
    seed_tool(desk, kind="mcp", name="postgres-server")
    assert _only(desk, "tool")["title"] == "An extension: postgres-server"


def test_each_kind_points_at_its_own_routes(desk):
    ids = seed_everything(desk)
    by = {i["kind"]: i for i in _inbox(desk)["items"]}
    want = {
        "preference": ("Keep", "Not this", f"/memory/preferences/{ids['preference']}/keep",
                       f"/memory/preferences/{ids['preference']}/decline"),
        "client": ("Yes, a client", "Not a client", f"/clients/{ids['client']}/confirm",
                   f"/clients/{ids['client']}/decline"),
        "skill": ("Approve", "Reject", f"/skills/proposals/{ids['skill']}/approve",
                  f"/skills/proposals/{ids['skill']}/reject"),
        "coach": ("Accept", "Decline", f"/coach/proposals/{ids['coach']}/accept",
                  f"/coach/proposals/{ids['coach']}/decline"),
        "habit": ("Accept", "Decline", f"/coach/proposals/{ids['habit']}/accept",
                  f"/coach/proposals/{ids['habit']}/decline"),
        "lesson": ("Accept", "Decline", f"/build/library/{ids['lesson']}/accept",
                   f"/build/library/{ids['lesson']}/decline"),
        "session_edit": ("Accept", "Decline", f"/build/library/{ids['session_edit']}/accept",
                         f"/build/library/{ids['session_edit']}/decline"),
        "memory": ("Approve", "Dismiss", f"/memory/review/{ids['memory']}/approve",
                   f"/memory/review/{ids['memory']}/dismiss"),
        "tool": ("Approve & create", "Reject", f"/capability/proposals/{ids['tool']}/approve",
                 f"/capability/proposals/{ids['tool']}/reject"),
        "wants": ("Approve and run", "Reject", f"/proposals/{ids['wants']}/approve",
                  f"/proposals/{ids['wants']}/reject"),
    }
    for kind, (yes, no, yes_path, no_path) in want.items():
        it = by[kind]
        assert (it["accept"]["label"], it["decline"]["label"]) == (yes, no), kind
        assert (it["accept"]["path"], it["decline"]["path"]) == (yes_path, no_path), kind
        assert it["accept"]["method"] == it["decline"]["method"] == "POST"
        assert it["accept"]["body"] == {} and it["decline"]["body"] == {}
        assert it["accept"]["note"], kind  # what accepting does is always said


def test_two_presses_only_where_an_answer_acts_at_once_or_is_final(desk):
    seed_everything(desk)
    by = {i["kind"]: i for i in _inbox(desk)["items"]}
    assert {k for k, i in by.items() if i["accept"] and i["accept"]["confirm"]} == {"memory", "tool", "wants"}
    # Rejecting something it wants to do is final, and two presses on its own screen.
    assert {k for k, i in by.items() if i["decline"] and i["decline"]["confirm"]} == {"wants"}


def test_an_offer_to_stop_asking_is_listed_but_never_decided_here(desk):
    gid = seed_goal_with_approved_asks(desk)
    it = _only(desk, "unasked")
    assert it["accept"] is None and it["decline"] is None
    assert it["home"] == {"href": "/autonomy", "label": C.OPEN_LABEL}
    assert it["extra"] == {"goal_id": gid, "tool": "web_fetch", "args_hash": ""}


def test_the_same_call_approved_every_time_is_offered_as_exactly_that(desk):
    gid = seed_goal(desk)
    seed_asks(desk, gid, tool="web_fetch", n=3, args_hash="h" * 16)
    it = _only(desk, "unasked")
    assert it["title"].startswith("Let the goal “Watch the tax rates” run exactly this without asking: ")
    assert it["extra"] == {"goal_id": gid, "tool": "web_fetch", "args_hash": "h" * 16}
    assert it["id"] == f"unasked:{gid}:web_fetch:{'h' * 16}"
    assert it["evidence"] == ["It asked 3 times and you approved every time."]
    # The same offers its own screen makes.
    with session_scope(desk.engine) as db:
        goal = db.get(GoalContractRecord, gid)
    rows = G.ask_rows_strict(desk.engine, gid)
    assert [o["args_hash"] for o in G.exact_offers(goal, G._hash_stats_from_rows(rows), desk.platform.grants)] == ["h" * 16]


def test_a_goal_whose_asks_were_not_all_approved_offers_nothing(desk):
    gid = seed_goal_with_approved_asks(desk, n=3)
    seed_asks(desk, gid, n=1, decision="deny")
    assert _inbox(desk) == NOTHING


def test_an_old_goal_is_still_looked_at(desk, monkeypatch):
    """Offers are looked for on the goals that have asks, however old: three
    newer goals with no asks must not push the one with an offer out."""
    monkeypatch.setattr(C, "GOALS_MAX", 2)
    old = seed_goal(desk, name="The old one", created_at=datetime.utcnow() - timedelta(days=400))
    seed_asks(desk, old)
    for i in range(3):
        seed_goal(desk, name=f"newer {i}")
    got = _inbox(desk)
    assert [i["extra"]["goal_id"] for i in got["items"]] == [old] and got["unreadable"] == []


def test_more_goals_with_asks_than_are_looked_at_is_said(desk, monkeypatch):
    monkeypatch.setattr(C, "GOALS_MAX", 2)
    for i in range(3):
        seed_asks(desk, seed_goal(desk, name=f"goal {i}"))
    got = _inbox(desk)
    assert got["unreadable"] == [{"kind": "unasked", "label": "Offers to stop asking"}]
    assert len(got["items"]) == 2  # what was looked at is still listed


def test_a_goals_grants_are_read_once(desk, monkeypatch):
    seed_goal_with_approved_asks(desk)
    calls = []
    real = desk.platform.grants.list
    monkeypatch.setattr(desk.platform.grants, "list", lambda *a, **k: calls.append(a) or real(*a, **k))
    assert _inbox(desk)["total"] == 1
    assert len(calls) == 1, calls


def test_every_home_link_is_the_screen_the_suggestion_lives_on(desk):
    seed_everything(desk)
    by = {i["kind"]: i for i in _inbox(desk)["items"]}
    assert {k: (i["home"] or {}).get("href") for k, i in by.items()} == {
        "preference": "/memory#prefs-suggested",
        "client": "/clients#waiting",
        "skill": "/skills",
        "coach": "/agents?view=team&agent=custom%3Atax-reader",
        "habit": "/agents?view=team&agent=custom%3Aglm-worker",
        "lesson": "/chat?view=code&session=chat_abc",
        "session_edit": "/chat?view=code&session=chat_abc",
        "memory": "/memory",
        "tool": "/tools",
        "wants": "/autonomy",
        "unasked": "/autonomy",
    }


def test_an_update_to_a_skill_is_shown_as_a_change(desk):
    seed_skill(desk, kind="refine")
    it = _only(desk, "skill")
    assert it["title"] == "Update the skill: month-end-close"
    assert it["detail"]["mode"] == "diff" and it["detail"]["before"].endswith("Old.")
    assert it["accept"]["note"] == "Approving updates that skill with this version."


def test_the_newest_comes_first(desk):
    old = datetime.utcnow() - timedelta(days=3)
    seed_skill(desk, name="older", created_at=old)
    seed_skill(desk, name="newest")
    seed_skill(desk, name="oldest", created_at=old - timedelta(days=3))
    seed_preference(desk, text="An older one", created_at=old)
    seed_preference(desk, text="The newest one")
    got = _inbox(desk)["items"]
    assert [i["about"] for i in got if i["kind"] == "skill"] == ["newest", "older", "oldest"]
    assert [i["title"] for i in got if i["kind"] == "preference"] == ["The newest one", "An older one"]


# ---- what an accept will really do -------------------------------------------
def test_a_memory_tidy_up_says_what_will_really_happen_not_what_the_model_said(desk):
    one, two = brain_notes(desk)
    seed_memory(desk, base="brain", survivor=one, remove=(one, two), says="Keep alpha and tidy the copy.")
    it = _only(desk, "memory")
    assert it["accept"]["note"] == (
        "Approving replaces everything in “hartleys”, and deletes 2 notes: “hartleys”, “hartleys-copy”. "
        "It can be undone from the Activity page.")
    assert it["accept"]["blocked"] == ""
    # The model's sentence is shown as what it SAID, and notes by name, not by path.
    assert it["evidence"] == ["Notes: hartleys, hartleys, hartleys-copy",
                              "The suggestion says: Keep alpha and tidy the copy."]
    assert desk.tmp.name not in json.dumps(it["evidence"])


def test_a_memory_tidy_up_for_a_base_that_is_not_connected_cannot_be_approved_here(desk):
    seed_memory(desk, base="notes")
    it = _only(desk, "memory")
    assert "connected right now" in it["accept"]["blocked"]
    # The same refusal the route gives.
    res = desk.client.post(it["accept"]["path"], json={})
    assert res.status_code == 409 and "connected" in res.json()["detail"]


def test_a_tidy_up_that_got_part_way_says_so(desk):
    rid = seed_memory(desk)
    with session_scope(desk.engine) as db:
        row = db.get(MemoryProposalRecord, rid)
        row.applied_json = json.dumps({"partial": True, "changed": ["a.md"]})
        db.add(row)
        db.commit()
    assert _only(desk, "memory")["evidence"][0] == C.MEMORY_PARTIAL


def test_approving_a_memory_tidy_up_through_the_inbox_changes_the_notes(desk):
    one, two = brain_notes(desk)
    seed_memory(desk, base="brain", survivor=one, remove=(two,), text="Hartleys, 1 High Street.")
    it = _only(desk, "memory")
    res = desk.client.post(it["accept"]["path"], json=it["accept"]["body"])
    assert res.status_code == 200, res.text
    assert Path(one).read_text(encoding="utf-8").strip() == "Hartleys, 1 High Street."
    assert not Path(two).exists()
    assert _inbox(desk) == NOTHING


def test_a_habit_says_so_when_team_notes_are_off(desk):
    seed_habit(desk)
    assert "is given with each piece of team work" in _only(desk, "habit")["accept"]["note"]
    desk.platform.config.team_notes = False
    assert _only(desk, "habit")["accept"]["note"] == C.HABIT_OFF_NOTE


def test_something_it_wants_to_do_cannot_be_started_under_emergency_stop(desk):
    seed_wants(desk)
    assert _only(desk, "wants")["accept"]["blocked"] == ""
    desk.platform.config.autonomy_kill_switch = True
    it = _only(desk, "wants")
    assert it["accept"]["blocked"] == C.STOPPED_BLOCK
    # It is blocked because the route itself refuses right now.
    assert desk.client.post(it["accept"]["path"], json={}).status_code == 403
    assert _status(desk, ProposalRecord, it["ref"]) == "pending"


def test_a_change_to_iron_jarvis_itself_needs_self_development_on(desk):
    seed_wants(desk, agent_type="maintainer", title="Fix a bug in Iron Jarvis")
    desk.platform.config.self_dev_enabled = False
    assert _only(desk, "wants")["accept"]["blocked"] == C.SELF_DEV_BLOCK
    desk.platform.config.self_dev_enabled = True
    assert _only(desk, "wants")["accept"]["blocked"] == ""


def test_a_tool_request_iron_jarvis_cannot_add_says_so_before_the_press(desk):
    seed_tool(desk, kind="mcp", name="postgres-server")
    it = _only(desk, "tool")
    assert it["accept"]["blocked"], it["accept"]
    seed_tool(desk, kind="tool", name="count-lines")
    ok = [i for i in _inbox(desk)["items"] if i["title"].endswith("count-lines")][0]
    assert ok["accept"]["blocked"] == ""


# ---- what is NOT listed -----------------------------------------------------
def test_an_answered_suggestion_is_not_listed(desk):
    seed_preference(desk, "Already kept", status="confirmed")
    seed_preference(desk, "Already declined", status="declined")
    seed_client(desk, "Hartleys", status="confirmed")
    seed_client(desk, "Smith", status="declined")
    seed_skill(desk, name="approved-one", status="approved")
    seed_skill(desk, name="rejected-one", status="rejected")
    for status in ("accepted", "declined", "stale"):
        seed_coach(desk, status=status)
        seed_lesson(desk, status=status, text=f"a {status} lesson")
    seed_memory(desk, status="dismissed")
    seed_tool(desk, name="old-tool", status="rejected")
    for status in ("approved", "rejected", "executed"):
        seed_wants(desk, status=status)
    assert _inbox(desk) == NOTHING


# ---- bounds -----------------------------------------------------------------
def test_a_long_list_is_cut_and_the_count_stays_true(desk, monkeypatch):
    monkeypatch.setattr(C, "PER_KIND", 3)
    for i in range(5):
        seed_skill(desk, name=f"skill-{i}")
        seed_lesson(desk, text=f"lesson {i}")
    got = _inbox(desk)
    assert got["groups"] == [{"kind": "skill", "label": "Skill drafts", "count": 5, "shown": 3},
                             {"kind": "lesson", "label": "Lessons from coding sessions", "count": 5, "shown": 3}]
    assert got["total"] == 10 and len(got["items"]) == 6


def test_no_more_rows_are_read_than_are_shown(desk, monkeypatch):
    """The read is bounded in the query itself, not after loading everything."""
    monkeypatch.setattr(C, "PER_KIND", 2)
    for i in range(6):
        seed_skill(desk, name=f"skill-{i}")
    built = []
    real = C.skill_item
    monkeypatch.setattr(C, "skill_item", lambda v: built.append(1) or real(v))
    assert _inbox(desk)["groups"][0] == {"kind": "skill", "label": "Skill drafts", "count": 6, "shown": 2}
    assert len(built) == 2


def test_a_long_text_is_cut_and_says_so(desk):
    seed_skill(desk, body="x" * (C.DETAIL_CHARS + 500))
    detail = _only(desk, "skill")["detail"]
    assert len(detail["after"]) == C.DETAIL_CHARS and detail["cut"] is True


def test_titles_and_reasons_are_one_bounded_line(desk):
    seed_preference(desk, text="line one\nline two " + "word " * 100)
    it = _only(desk, "preference")
    assert "\n" not in it["title"] and len(it["title"]) <= C.TITLE_CHARS and it["title"].endswith("…")


def test_a_long_lesson_keeps_its_whole_text_under_the_title(desk):
    long = "Always check the ledger first. " * 12
    seed_lesson(desk, text=long)
    it = _only(desk, "lesson")
    assert it["title"].endswith("…") and it["detail"]["mode"] == "text"
    assert it["detail"]["after"] == long


def test_only_a_few_lines_of_evidence_are_listed(desk):
    rid = seed_preference(desk)
    with session_scope(desk.engine) as db:
        row = db.get(LessonRecord, rid)
        row.evidence_json = json.dumps([{"quote": f"said {i}", "at": "", "where": "chat"} for i in range(9)])
        db.add(row)
        db.commit()
    assert len(_only(desk, "preference")["evidence"]) == C.EVIDENCE_LINES


# ---- a store that cannot be read --------------------------------------------
TABLES = [
    (LessonRecord, ["preference"]),
    (WorkClient, ["client"]),
    (SkillProposalRecord, ["skill"]),
    (CoachProposalRecord, ["coach", "habit"]),
    (HarnessLibraryRecord, ["lesson", "session_edit"]),
    (MemoryProposalRecord, ["memory"]),
    (CapabilityProposalRecord, ["tool"]),
    (ProposalRecord, ["wants"]),
    (GoalContractRecord, ["unasked"]),
    (EventRecord, ["unasked"]),
]


@pytest.mark.parametrize("model,kinds", TABLES, ids=[m.__name__ for m, _k in TABLES])
def test_a_store_that_cannot_be_read_is_named_and_hides_nothing_else(desk, model, kinds):
    """Each kind's own table is taken away: that kind is NAMED as unread (it is
    never "nothing waiting"), and every other kind is still listed."""
    seed_everything(desk)
    table = model.__tablename__
    with desk.engine.begin() as conn:
        conn.exec_driver_sql(f"ALTER TABLE {table} RENAME TO {table}_away")
    got = C.collect(desk.app.state.d)
    assert [u["kind"] for u in got["unreadable"]] == kinds
    assert [u["label"] for u in got["unreadable"]] == [C.LABELS[k] for k in kinds]
    assert [g["kind"] for g in got["groups"]] == [k for k, _l in C.KINDS if k not in kinds]


def test_one_odd_row_never_hides_its_kind(desk):
    seed_skill(desk, name="fine")
    seed_skill(desk, name="odd", source_session_ids="7")  # not a list of runs
    got = _inbox(desk)
    assert got["unreadable"] == []
    assert sorted(i["about"] for i in got["items"]) == ["fine", "odd"]
    odd = [i for i in got["items"] if i["about"] == "odd"][0]
    assert odd["evidence"] == []


def test_a_row_that_cannot_be_shown_is_skipped_and_the_count_says_one_more(desk, monkeypatch):
    seed_skill(desk, name="fine")
    seed_skill(desk, name="broken")
    real = C.skill_item

    def build(view):
        if view.get("skill_name") == "broken":
            raise ValueError("cannot show this one")
        return real(view)

    monkeypatch.setattr(C, "skill_item", build)
    got = _inbox(desk)
    assert got["groups"] == [{"kind": "skill", "label": "Skill drafts", "count": 2, "shown": 1}]
    assert [i["about"] for i in got["items"]] == ["fine"] and got["unreadable"] == []


def test_a_build_without_a_service_lists_nothing_for_it(desk, monkeypatch):
    seed_everything(desk)
    monkeypatch.setattr(desk.platform, "coach", None, raising=False)
    monkeypatch.setattr(desk.platform, "intent", None, raising=False)
    got = _inbox(desk)
    assert got["unreadable"] == []
    assert {g["kind"] for g in got["groups"]} == {k for k, _l in C.KINDS} - {"coach", "habit", "wants"}


# ---- it only reads ----------------------------------------------------------
def test_reading_the_inbox_changes_nothing(desk):
    seed_everything(desk)
    tables = (LessonRecord, SkillProposalRecord, CoachProposalRecord, HarnessLibraryRecord,
              MemoryProposalRecord, CapabilityProposalRecord, ProposalRecord, EventRecord, GoalContractRecord,
              WorkClient, WorkClientFolder)

    def snapshot():
        with session_scope(desk.engine) as db:
            return [sorted(json.dumps(r.model_dump(), default=str, sort_keys=True) for r in db.exec(select(t)))
                    for t in tables]

    before = snapshot()
    for _ in range(3):
        _inbox(desk)
    assert snapshot() == before


# ---- a decision is the suggestion's own route --------------------------------
def _press(desk, act):
    return desk.client.request(act["method"], act["path"], json=act["body"])


DECLINED = {
    "preference": (LessonRecord, "declined"), "client": (WorkClient, "declined"),
    "skill": (SkillProposalRecord, "rejected"),
    "coach": (CoachProposalRecord, "declined"), "habit": (CoachProposalRecord, "declined"),
    "lesson": (HarnessLibraryRecord, "declined"), "session_edit": (HarnessLibraryRecord, "declined"),
    "memory": (MemoryProposalRecord, "dismissed"), "tool": (CapabilityProposalRecord, "rejected"),
    "wants": (ProposalRecord, "rejected"),
}


@pytest.mark.parametrize("kind,seed", [
    ("preference", seed_preference), ("client", seed_client), ("skill", seed_skill), ("coach", seed_coach),
    ("habit", seed_habit), ("lesson", seed_lesson), ("session_edit", seed_session_edit), ("memory", seed_memory),
    ("tool", seed_tool), ("wants", seed_wants),
])
def test_saying_no_through_the_inbox_is_that_kinds_own_no(desk, kind, seed):
    if kind == "client" and not _clients_routes(desk):
        pytest.skip("the client's own routes (builder A, daemon/routes/clients.py) are not in this build yet")
    rid = seed(desk)
    it = _only(desk, kind)
    res = _press(desk, it["decline"])
    assert res.status_code == 200, (kind, res.status_code, res.text)
    model, status = DECLINED[kind]
    assert _status(desk, model, rid) == status
    assert _inbox(desk) == NOTHING


def test_keeping_a_preference_through_the_inbox_keeps_it(desk):
    pid = seed_preference(desk)
    res = _press(desk, _only(desk, "preference")["accept"])
    assert res.status_code == 200, res.text
    assert _inbox(desk) == NOTHING
    kept = [p["id"] for p in prefs.list_preferences(desk.engine)["kept"]]
    assert pid in kept


def test_approving_a_skill_through_the_inbox_saves_the_skill(desk):
    seed_skill(desk)
    res = _press(desk, _only(desk, "skill")["accept"])
    assert res.status_code == 200, res.text
    assert _inbox(desk) == NOTHING
    assert (desk.platform.config.home / "skills" / "month-end-close" / "SKILL.md").exists()


def test_accepting_a_lesson_through_the_inbox_keeps_the_lesson(desk):
    rid = seed_lesson(desk, folder=str(desk.tmp / "books"))
    (desk.tmp / "books").mkdir()
    res = _press(desk, _only(desk, "lesson")["accept"])
    assert res.status_code == 200, res.text
    assert _status(desk, HarnessLibraryRecord, rid) == "accepted"


def test_accepting_a_coach_change_through_the_inbox_changes_the_instructions(desk):
    made = desk.client.post("/agents", json={"name": "tax-reader", "system_prompt": "Read the return.",
                                             "description": "reads returns"})
    assert made.status_code == 200, made.text
    rid = seed_coach(desk)
    res = _press(desk, _only(desk, "coach")["accept"])
    assert res.status_code == 200, res.text
    assert _status(desk, CoachProposalRecord, rid) == "accepted"
    assert desk.platform.agents_registry.get("tax-reader").system_prompt == "Read the return. Always name the form."


def test_a_coach_change_gone_stale_is_refused_in_the_routes_words(desk):
    desk.client.post("/agents", json={"name": "tax-reader", "system_prompt": "Something else now.",
                                      "description": "reads returns"})
    rid = seed_coach(desk)
    res = _press(desk, _only(desk, "coach")["accept"])
    assert res.status_code == 409 and res.json()["detail"]
    assert _status(desk, CoachProposalRecord, rid) == "stale"
    assert _inbox(desk) == NOTHING  # it is no longer waiting


def test_approving_a_tool_request_through_the_inbox_creates_the_tool(desk):
    rid = seed_tool(desk, name="count_lines")
    res = _press(desk, _only(desk, "tool")["accept"])
    assert res.status_code == 200, res.text
    assert _status(desk, CapabilityProposalRecord, rid) == "approved"
    assert "count_lines" in desk.platform.registry.names()
    assert _inbox(desk) == NOTHING


def test_the_inbox_has_no_decision_code_of_its_own():
    src = inspect.getsource(C)
    assert "def collect(" in src and len(src) > 5000  # the real module, not an empty one
    for word in (".accept(", ".decline(", ".approve(", ".reject(", ".dismiss(", ".keep(", "db.add(", "db.delete(",
                 "db.commit(", "db.merge(", ".apply("):
        assert word not in src, word
    # Every decision it hands out goes to one of the eight suggestion routes.
    assert set(re.findall(r'f"(/[a-z/]+/)\{_seg\((?:pid|rid)\)\}/', src)) == {
        "/build/library/", "/capability/proposals/", "/clients/", "/coach/proposals/", "/memory/preferences/",
        "/memory/review/", "/proposals/", "/skills/proposals/"}


# ---- clients (v1.499.0, vision plan leap 3 Phase 3) ---------------------------
def test_client_evidence_names_folders_not_paths(desk):
    seed_client(desk, evidence_json=json.dumps({"rule": "under_clients", "work": 4, "folders": 2, "homes": [
        {"folder": "C:\\Users\\Pat\\Documents\\Clients\\Hartleys", "work": 2, "last_at": "", "local": True},
        {"folder": "\\\\fileserver\\share\\Clients\\Hartleys 2025", "work": 2, "last_at": "", "local": False},
    ]}))
    it = _only(desk, "client")
    assert it["evidence"] == ["Clients › Hartleys: 2 pieces of work", "Clients › Hartleys 2025: 2 pieces of work"]
    said = json.dumps(it, ensure_ascii=False)
    for part in ("C:", "Users", "Pat", "Documents", "fileserver", "share", "\\\\"):
        assert part not in said, part
    # One press each, both to the client's own routes; its home is the review.
    assert it["accept"]["confirm"] is False and it["decline"]["confirm"] is False
    assert it["accept"]["note"] == ("Gives Hartleys a page and keeps its folders, work and preferences together. "
                                    "Chats are told about Hartleys only in Hartleys' folders.")
    assert it["decline"]["note"] == "Iron Jarvis won't ask about Hartleys again."
    assert it["home"] == {"href": "/clients#waiting", "label": C.OPEN_LABEL}
    assert C.folder_label("C:\\Clients\\Hartleys\\2025") == "Hartleys › 2025"
    assert C.folder_label("/home/pat/Clients/Smith") == "Clients › Smith"
    # A folder right under the user's own folder never shows the user's name.
    assert C.folder_label("C:\\Users\\Pat\\Hartleys") == "Hartleys"
    assert C.folder_label("/home/pat/Smith") == "Smith"
    assert C.folder_label("\\\\fileserver\\share\\Hartleys") == "Hartleys"


def test_yes_through_the_inbox_confirms_the_client_and_links_its_folders(desk):
    if not _clients_routes(desk):
        pytest.skip("the client's own routes (builder A, daemon/routes/clients.py) are not in this build yet")
    cid = seed_client(desk)
    res = _press(desk, _only(desk, "client")["accept"])
    assert res.status_code == 200, res.text
    assert _status(desk, WorkClient, cid) == "confirmed"
    with session_scope(desk.engine) as db:
        linked = [r.folder for r in db.exec(select(WorkClientFolder).where(WorkClientFolder.client_id == cid))]
    assert linked, "confirming links the folders the name was seen in"
    assert [i for i in _inbox(desk)["items"] if i["kind"] == "client"] == []
