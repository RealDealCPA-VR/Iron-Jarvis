"""v1.421.0 (Wave 1, A4, the owner's answer) — the reviewer says EXACTLY what
needs correcting, so the lead can hand each point to the worker who made it.

The owner: "The reviewer should get read-only commands, but also be able to
determine exactly what needs correcting for shooting over to the team leader
for work redistribution." What these pins hold:

1. THE FORM: the review brief (the finish review's and the CI reviewer's)
   ends with the findings form — one ``R<n>. <path>:<line> — what is wrong —
   what to change`` line per problem (``R<n>. (general) — …`` with no single
   place), ABOVE the verdict line; ``team_record.VERDICT_ASK``'s last-line
   rule is unchanged and comes after it. The prefix is stable and
   regex-friendly (``finish.FINDING_RX``) for the later Team-notes wave.
2. WHO CHANGED WHAT: when the reply's pieces are on the model team's record
   (A2: each piece's member and the files its session wrote), the brief lists
   file → member; absent with no team or no record.
3. THE LEAD SEES IT WHOLE: a long report's prose may be cut, its findings
   never (``finish.report_for_lead``); the note repeats who changed what and
   tells the lead to hand each finding to the worker who changed that file,
   by name — or do it itself only when Iron Jarvis says no worker can take it
   (``harness/team``'s own rule).
"""

from __future__ import annotations

import json

from sqlmodel import select

from iron_jarvis.agents import team_record as TR
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType, Session, ToolInvocation
from iron_jarvis.harness import ci_fix
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import helpers as H
from iron_jarvis.harness import team as T
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_session_team_v1403 import (  # noqa: F401 — `world` is a fixture
    answer_note,
    body,
    members,
    reviewer_calls,
    stream,
    world,
    write_x,
)

#: The verdict ask as v1.395.0 shipped it — unchanged (its last-line rule).
VERDICT_ASK_V1395 = (
    "End your report with exactly one line that gives your verdict on the change: "
    "\"VERDICT: approved\" or \"VERDICT: changes needed\"."
)


def _named(cid: str, who: str, task: str) -> list[ToolCall]:
    return [ToolCall(id=cid, name="delegate", arguments={"agent_type": f"custom:{who}", "task": task})]


# --------------------------------------------------------------------------- #
# 1. the form
# --------------------------------------------------------------------------- #


def test_the_brief_offers_read_only_git_and_ends_with_the_findings_form():
    assert "git_read" in F.REVIEW_BRIEF_LEAD and "tests included" in F.REVIEW_BRIEF_LEAD
    brief = F.review_brief("set X to 2", "I set X", "+X = 2")
    assert brief.startswith(F.REVIEW_BRIEF_LEAD)
    assert brief.endswith(F.REVIEW_FINDINGS_ASK)
    assert "R1. <path>:<line> — what is wrong — what to change" in F.REVIEW_FINDINGS_ASK
    assert "R2. (general) — what is wrong — what to change" in F.REVIEW_FINDINGS_ASK
    assert "above your verdict line" in F.REVIEW_FINDINGS_ASK
    # The verdict's own rule is exactly what it was.
    assert TR.VERDICT_ASK == VERDICT_ASK_V1395


def test_the_ci_reviewers_brief_has_the_form_with_the_verdict_last():
    task = ci_fix.review_task("make CI green", "fixed the import", "+import os")
    assert task.endswith(F.REVIEW_FINDINGS_ASK + "\n\n" + TR.VERDICT_ASK)
    assert "git_read" in task


def test_the_finding_prefix_is_stable_and_reads_only_finding_lines():
    yes = ["R1. app.py:3 — X is 1 — set it to 2", "R12. (general) — no test — add one",
           "  R3. src/a.py:10 — x — y"]
    no = ["R. app.py — x", "Rx. a — b", "1. app.py:3 — x", "VERDICT: approved", "Run R1. again",
          "R1.app.py:3"]
    assert all(F.FINDING_RX.match(s) for s in yes)
    assert not any(F.FINDING_RX.match(s) for s in no)
    assert [F.FINDING_RX.match(s).group(1) for s in yes] == ["1", "12", "3"]


async def test_the_finish_reviewers_task_carries_the_form_then_the_verdict_last(world):  # noqa: F811
    members(world.p)
    world.main = [_named("n1", "qwen", "Set X to 2 in app.py."), "The worker set X to 2.",
                  answer_note(world)]
    world.worker = [write_x(2), "Changed app.py."]
    world.reviewer = ["R1. app.py:1 — fine — nothing\nVERDICT: approved"]
    await stream(world.p, body(world.ws))
    task = reviewer_calls(world)[0]["task"]
    assert task.startswith(F.REVIEW_BRIEF_LEAD)
    assert task.rstrip().endswith(F.REVIEW_FINDINGS_ASK + "\n\n" + TR.VERDICT_ASK)


# --------------------------------------------------------------------------- #
# 2. who changed what
# --------------------------------------------------------------------------- #


async def test_the_brief_and_the_leads_note_say_who_changed_what(world):  # noqa: F811
    members(world.p)
    world.main = [_named("n1", "qwen", "Set X to 2 in app.py."), "The worker set X to 2.",
                  answer_note(world)]
    world.worker = [write_x(2), "Changed app.py."]
    world.reviewer = ["R1. app.py:1 — X should be 3 — set X = 3\nVERDICT: changes needed"]
    await stream(world.p, body(world.ws))
    task = reviewer_calls(world)[0]["task"]
    assert F.WHO_CHANGED_HEAD in task and F.WHO_CHANGED_LEAD in task
    assert "- app.py — qwen" in task
    # Listed after the change, before the findings form.
    assert task.index(F.CHANGE_ENDS) < task.index(F.WHO_CHANGED_HEAD) < task.index(F.REVIEW_FINDINGS_ASK)
    (note,) = world.notes
    assert F.WHO_CHANGED_HEAD in note and "- app.py — qwen" in note
    assert "R1. app.py:1 — X should be 3 — set X = 3" in note
    assert F.TEAM_REVIEW_ASK in note


def test_the_list_is_absent_with_no_record():
    assert F.WHO_CHANGED_HEAD not in F.review_brief("r", "d", "+x")
    assert F.WHO_CHANGED_HEAD not in F.review_brief("r", "d", "+x", [])
    assert F.who_changed_what(None, "build:x", 0) == []


def _seed_reply(p, thread: str, *, record: bool) -> int:
    """Two helper sessions of a Build session that wrote ledger rows above the
    mark; with ``record`` their pieces are on the team's record."""
    mark = F.ledger_mark(p.engine)
    with session_scope(p.engine) as db:
        for sid in ("h-q", "h-g"):
            db.add(Session(id=sid, task="t", agent_type=AgentType.BUILDER,
                           options_json=json.dumps({H.BUILD_SESSION_OPTION: thread})))
            db.add(ToolInvocation(session_id=sid, agent_run_id="r", tool="write_file"))
        db.commit()
    if record:
        TR.note_run(p.engine, member="qwen", role="code", session_id="h-q", scope=f"build:{thread}",
                    finished=True, asked="name", files=["app.py", "util.py"])
        TR.note_run(p.engine, member="glm", role="test", session_id="h-g", scope=f"build:{thread}",
                    finished=True, asked="role", files=["util.py", "test_app.py"])
        TR.note_run(p.engine, member="rev", role="review", session_id="h-g", scope=f"build:{thread}",
                    finished=True, files=["review-notes.md"])
    return mark


def test_who_changed_what_reads_the_replys_pieces_on_the_record(tmp_path):
    p = build_platform(str(tmp_path))
    mark = _seed_reply(p, "t1", record=True)
    who = F.who_changed_what(p.engine, "build:t1", mark)
    assert who == [("app.py", "qwen"), ("util.py", "qwen"), ("util.py", "glm"), ("test_app.py", "glm")]
    assert F.who_changed_lines(who) == ["- app.py — qwen", "- util.py — qwen, glm", "- test_app.py — glm"]
    # Pieces of an EARLIER reply (below the mark) are not this reply's.
    later = F.ledger_mark(p.engine)
    assert F.who_changed_what(p.engine, "build:t1", later) == []


def test_who_changed_what_is_empty_without_a_record(tmp_path):
    p = build_platform(str(tmp_path))
    mark = _seed_reply(p, "t2", record=False)
    assert F.who_changed_what(p.engine, "build:t2", mark) == []
    with session_scope(p.engine) as db:
        assert list(db.exec(select(TR.TeamRoleRun))) == []


def test_the_list_is_bounded_and_masked():
    key = "sk-ant-api03-" + "Z" * 40
    who = [(f"f{i}.py", "qwen") for i in range(F.MAX_WHO_FILES + 7)] + [(f"{key}.txt", "glm")]
    lines = F.who_changed_lines(who)
    assert len(lines) == F.MAX_WHO_FILES + 1 and lines[-1] == "(and 8 more files)"
    assert not any(key in ln for ln in F.who_changed_lines([(f"{key}.txt", "glm")]))


# --------------------------------------------------------------------------- #
# 3. the lead sees every finding
# --------------------------------------------------------------------------- #


def test_a_short_report_reaches_the_lead_unchanged():
    report = "Looked at it.\nR1. app.py:1 — x — y\nVERDICT: changes needed"
    assert F.report_for_lead(report) == report


def test_a_long_report_keeps_every_finding_and_its_verdict_whole():
    findings = [f"R{i}. src/m{i}.py:{i} — " + ("w" * 300) + f" — fix {i}" for i in range(1, 31)]
    prose = "I read every file. " * 400
    report = prose + "\n" + "\n".join(findings) + "\nVERDICT: changes needed"
    out = F.report_for_lead(report)
    for f in findings:
        assert f in out, f[:30]
    assert out.rstrip().endswith("VERDICT: changes needed")
    assert F.PROSE_CUT_NOTE in out
    assert len(out) < len(report)
    # Findings stay in their order, after the kept prose.
    assert out.index("R1. ") < out.index("R2. ") < out.index("R30. ")


def test_past_the_bound_it_says_how_many_findings_were_left_out():
    findings = [f"R{i}. a.py:{i} — x — y" for i in range(1, F.MAX_REVIEW_FINDINGS + 6)]
    report = "p " * 3000 + "\n" + "\n".join(findings) + "\nVERDICT: changes needed"
    out = F.report_for_lead(report)
    assert f"R{F.MAX_REVIEW_FINDINGS}. a.py" in out
    assert f"R{F.MAX_REVIEW_FINDINGS + 1}. a.py" not in out
    assert "[and 5 more findings]" in out


def test_a_long_report_with_no_finding_lines_is_cut_as_before():
    out = F.report_for_lead("words " * 2000)
    assert out.endswith("[the rest of its report is left out]")


async def test_through_the_lane_a_long_report_reaches_the_lead_with_every_finding(world):  # noqa: F811
    members(world.p)
    findings = [f"R{i}. app.py:{i} — " + ("problem " * 40).strip() + f" — change {i}" for i in range(1, 9)]
    long_report = ("I read app.py and the change. " * 300) + "\n" + "\n".join(findings) + "\nVERDICT: changes needed"
    assert len(long_report) > F.MAX_REVIEW_REPORT_CHARS * 2
    world.main = [_named("n1", "qwen", "Set X to 2 in app.py."), "The worker set X to 2.",
                  answer_note(world)]
    world.worker = [write_x(2), "Changed app.py."]
    world.reviewer = [long_report]
    out = await stream(world.p, body(world.ws))
    assert out["done"]["finish"]["review"]["verdict"] == "changes_needed"
    note = world.notes[0]
    for f in findings:
        assert f in note, f[:20]
    assert "VERDICT: changes needed" in note
    assert F.PROSE_CUT_NOTE in note


def test_the_leads_ask_routes_each_finding_by_name_as_the_team_rules_do():
    ask = F.TEAM_REVIEW_ASK
    assert "worker who changed that file, by name" in ask and "R number" in ask
    assert "only when Iron Jarvis tells you that no worker can take it may you do it yourself" in ask
    # The team's own rule, in the same words (harness/team).
    assert "Only when Iron Jarvis tells you that no worker can take a piece may you do that piece yourself" \
        in T.TEAM_BRIEF_HANDS
    assert "R numbers" in F.REVIEW_ASK
