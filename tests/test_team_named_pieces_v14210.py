"""v1.421.0 (Wave 1, A2) — every piece a model-team member works is on its
record, not only one asked for by ROLE.

The live record: 2 ``TeamRoleRun`` rows ever, while one coding session handed
~14 pieces to its glm worker BY NAME. Both helper doors (``delegate`` and
``spawn_agent``, lock-step) set the recorded role only from ``role:<id>``, so
``model_team.note_piece`` skipped every named piece. Now
``model_team.piece_role`` infers the role the member worked as and the row
says HOW it was asked (``asked``: role / name), with the files its session
wrote (``files_json``) for the coming Team notes.

What these pins hold (the stream lane, the real doors, the real finish check
and team review, scripted models — the harness of
``tests/test_session_team_v1403.py``):

1. a worker named by the lead of a coding session's team: a ``code`` row,
   ``asked == "name"``, the files its session wrote; the finish check's
   verdict AND the team review's verdict reach that row (``credit_check`` by
   session, ``credit_review`` by scope + reply);
2. the session's reviewer named by the lead: a ``review`` row, asked by name,
   its task handed on UNCHANGED (no verdict line asked of a named piece);
3. ``spawn_agent`` records a named member the same way;
4. a named piece to a NON-member writes no row;
5. ``piece_role``'s table (role asked / held / not held; reviewer; worker by
   base type; outside a team: the only role, else the inference);
6. the router's record and ``GET /team`` count a named piece;
7. an OLDER ``teamrolerun`` table gains both columns at boot and its rows
   read as before.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.agents import model_team as MT
from iron_jarvis.agents import team_record as TR
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentType
from iron_jarvis.harness import helpers as H
from iron_jarvis.harness import team as T
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_session_team_v1403 import (  # noqa: F401 — `world` is a fixture
    LOCAL,
    TEAM,
    answer_note,
    body,
    helper_sessions,
    members,
    reviewer_calls,
    stream,
    world,
    write_x,
)


def _rows(p) -> list[TR.TeamRoleRun]:
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(TR.TeamRoleRun)))
        for r in rows:
            db.expunge(r)
        return rows


def _named(cid: str, who: str, task: str) -> list[ToolCall]:
    """The lead hands a piece BY NAME (no role) — what the live session did."""
    return [ToolCall(id=cid, name="delegate", arguments={"agent_type": f"custom:{who}", "task": task})]


# --------------------------------------------------------------------------- #
# 1-2. a coding session's team, through the real stream lane
# --------------------------------------------------------------------------- #


async def test_a_worker_named_by_the_lead_is_on_its_record_with_its_files_and_both_verdicts(world):
    members(world.p)
    world.main = [_named("n1", "qwen", "Set X to 2 in app.py."), "The worker set X to 2.",
                  answer_note(world)]
    world.worker = [write_x(2), "Changed app.py."]
    world.reviewer = ["Good.\nVERDICT: approved"]
    out = await stream(world.p, body(world.ws))
    (kid_q, *_rest) = [k for k in helper_sessions(world.p) if k.agent_name == "custom:qwen"]
    code = [r for r in _rows(world.p) if r.member == "qwen"]
    assert len(code) == 1, [(r.member, r.role, r.asked) for r in _rows(world.p)]
    row = code[0]
    assert (row.role, row.asked, row.finished) == ("code", TR.ASKED_BY_NAME, True)
    assert row.session_id == kid_q.id and row.scope == "build:team-session"
    assert TR.files_of(row) == ["app.py"]
    assert row.piece == MT.piece_key(None, "Set X to 2 in app.py.")
    # The team review's verdict reached it (by scope + reply): a named code
    # piece is part of the reply's work, judged like a role's.
    assert out["done"]["finish"]["review"]["verdict"] == "approved"
    assert row.review == TR.REVIEW_GOOD
    # The finish check credits by the helper's SESSION — the reply's helpers
    # that changed files (its own gathering): no tests ran here, so nothing
    # was credited; the named worker IS among those a verdict would reach.
    from iron_jarvis.harness import finish as F

    assert row.check_result == ""
    assert kid_q.id in F.helpers_that_changed(world.p.engine, "build:team-session", 0)
    assert TR.credit_check(world.p.engine, [kid_q.id], TR.CHECK_PASSED) == 1
    assert next(r for r in _rows(world.p) if r.member == "qwen").check_result == TR.CHECK_PASSED
    review_rows = [r for r in _rows(world.p) if r.member == "rev"]
    assert [(r.role, r.asked) for r in review_rows] == [("review", TR.ASKED_BY_ROLE)]


async def test_the_reviewer_named_by_the_lead_is_a_review_row_and_its_task_is_unchanged(world):
    members(world.p)
    world.main = [_named("r1", "rev", "Review app.py."), "The reviewer read it."]
    world.reviewer = ["Looks fine."]
    await stream(world.p, body(world.ws, "review app.py"))
    rev = [r for r in _rows(world.p) if r.member == "rev" and r.asked == TR.ASKED_BY_NAME]
    assert [(r.role, r.finished) for r in rev] == [("review", True)]
    assert TR.files_of(rev[0]) == []  # it only read
    # A named piece's task is handed on as the lead wrote it: no verdict line
    # is asked of it (only a review the planner ASKED for carries one).
    first = reviewer_calls(world)[0]
    assert "Review app.py." in first["task"] and TR.VERDICT_ASK not in first["task"]


# --------------------------------------------------------------------------- #
# 3-4. spawn_agent, and a non-member
# --------------------------------------------------------------------------- #


def _ctx(p, sid: str, ws):
    from iron_jarvis.tools.base import ToolContext

    return ToolContext(workspace=ws, session_id=sid, agent_run_id="parent-run",
                       config=p.config, event_bus=p.event_bus, engine=p.engine)


async def _parent(p) -> str:
    from iron_jarvis.agents.orchestrator import Orchestrator

    s = await Orchestrator(p).create_session(
        "the parent's task", AgentType.SUPERVISOR, provider="google", model="gemini-3.8-flash"
    )
    return s.id


async def test_spawn_agent_records_a_named_member_like_delegate(tmp_path, monkeypatch):
    from tests._fakes.team_world_v1395 import make_world, member

    w = make_world(tmp_path, monkeypatch)
    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("read", "code"))
    member(w.p, "scribe", LOCAL[0], LOCAL[1], roles=("read",))
    sid = await _parent(w.p)
    res = await w.p.registry.get("spawn_agent").execute(
        {"agent": "glm", "task": "Write util.py."}, _ctx(w.p, sid, tmp_path))
    assert res.ok, res.error
    res = await w.p.registry.get("spawn_agent").execute(
        {"agent": "scribe", "task": "Summarize util.py."}, _ctx(w.p, sid, tmp_path))
    assert res.ok, res.error
    got = [(r.member, r.role, r.asked, r.scope) for r in _rows(w.p)]
    # glm holds read + code and is builder-based: code; scribe's only role: read.
    assert got == [("glm", "code", TR.ASKED_BY_NAME, sid), ("scribe", "read", TR.ASKED_BY_NAME, sid)]


async def test_a_named_piece_to_a_non_member_writes_no_row(tmp_path, monkeypatch):
    from tests._fakes.team_world_v1395 import make_world

    w = make_world(tmp_path, monkeypatch)
    # A custom agent with NO roles is no member; a built-in helper neither.
    w.p.agents_registry.register("plain", "You are plain.", [], provider=LOCAL[0], model=LOCAL[1])
    sid = await _parent(w.p)
    for args in ({"agent_type": "custom:plain", "task": "Do a thing."},
                 {"agent_type": "researcher", "task": "Look something up."}):
        res = await w.p.registry.get("delegate").execute(args, _ctx(w.p, sid, tmp_path))
        assert res.ok, res.error
    res = await w.p.registry.get("spawn_agent").execute(
        {"agent": "plain", "task": "Do another."}, _ctx(w.p, sid, tmp_path))
    assert res.ok, res.error
    assert _rows(w.p) == []


# --------------------------------------------------------------------------- #
# 5. the inference table
# --------------------------------------------------------------------------- #


def _rec(name: str, *roles: str):
    return SimpleNamespace(name=name, roles_json=json.dumps(list(roles)))


def _turn(workers=("qwen",), reviewer="rev"):
    return T.TeamTurn(team=T.SessionTeam(workers=tuple(workers), reviewer=reviewer))


@pytest.mark.parametrize("rec,kw,want", [
    # A role the planner asked for: that role when held, else no row.
    (_rec("glm", "read", "code"), {"asked": "read"}, ("read", "role")),
    (_rec("glm", "read", "code"), {"asked": "review"}, ("", "")),
    # Inside a coding session's team: the reviewer reviews …
    (_rec("rev", "code", "review"), {"team": _turn()}, ("review", "name")),
    # … a worker by what the call asked: a read-only base reads …
    (_rec("qwen", "read", "code", "test"), {"team": _turn(), "base_type": AgentType.RESEARCHER},
     ("read", "name")),
    (_rec("qwen", "read", "code", "test"), {"team": _turn(), "base_type": "reviewer"}, ("read", "name")),
    # … otherwise code when held …
    (_rec("qwen", "read", "code", "test"), {"team": _turn(), "base_type": AgentType.BUILDER},
     ("code", "name")),
    # … else the first role it holds in ROLES order.
    (_rec("qwen", "test", "read"), {"team": _turn(), "base_type": "builder"}, ("read", "name")),
    (_rec("qwen", "test"), {"team": _turn(), "base_type": "builder"}, ("test", "name")),
    # A read-only base that does not hold `read` falls through (no row for a
    # role it does not hold).
    (_rec("qwen", "code", "test"), {"team": _turn(), "base_type": "researcher"}, ("code", "name")),
    # Outside a session's team: the only role …
    (_rec("scribe", "review"), {}, ("review", "name")),
    # … else the same inference.
    (_rec("glm", "read", "code"), {}, ("code", "name")),
    (_rec("glm", "read", "code"), {"base_type": "researcher"}, ("read", "name")),
    (_rec("both", "review", "test"), {}, ("review", "name")),
    # A non-member, or no record: nothing.
    (_rec("plain"), {}, ("", "")),
    (None, {"asked": "code"}, ("", "")),
])
def test_the_role_a_piece_is_recorded_under(rec, kw, want):
    assert MT.piece_role(rec, **kw) == want


def test_the_row_keeps_how_it_was_asked_and_a_bounded_masked_file_list(tmp_path):
    p = build_platform(str(tmp_path))
    files = [f"src/f{i}.py" for i in range(TR.MAX_PIECE_FILES + 10)]
    files.insert(1, "src/f0.py")  # a repeat
    files.insert(2, "keys/sk-ant-api03-" + "A" * 40 + ".txt")  # a key named as a file
    files.insert(3, "x" * 1000)
    rid = TR.note_run(p.engine, member="m", role="code", session_id="s", scope="sc", finished=True,
                      asked="name", files=files)
    rid2 = TR.note_run(p.engine, member="m", role="code", session_id="s2", scope="sc", finished=True,
                       asked="by magic")
    rows = {r.id: r for r in _rows(p)}
    kept = TR.files_of(rows[rid])
    assert rows[rid].asked == "name" and rows[rid2].asked == ""
    assert len(kept) == TR.MAX_PIECE_FILES and kept.count("src/f0.py") == 1
    assert all(len(f) <= TR.MAX_FILE_CHARS for f in kept)
    assert not any("sk-ant-api03-" + "A" * 40 in f for f in kept)
    assert TR.files_of(rows[rid2]) == []


# --------------------------------------------------------------------------- #
# 6. the router's record and GET /team count a named piece
# --------------------------------------------------------------------------- #


async def test_the_routers_record_and_the_team_view_count_a_named_piece(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes.team import team_view
    from tests._fakes.team_world_v1395 import make_world, member

    w = make_world(tmp_path, monkeypatch)
    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("code",))
    sid = await _parent(w.p)
    for i in range(3):
        res = await w.p.registry.get("delegate").execute(
            {"agent_type": "custom:glm", "task": f"Write part {i}."}, _ctx(w.p, sid, tmp_path))
        assert res.ok, res.error
    rec = TR.role_record(w.p.engine, "glm", "code", (LOCAL[0], LOCAL[1]))
    assert (rec.runs, rec.passed, rec.status) == (3, 3, TR.GOOD)
    glm = next(m for m in team_view(w.p)["members"] if m["name"] == "glm")
    assert glm["records"]["code"]["words"] == "good (3 of 3 passed)"
    # … and the router reads the same record (a role piece: "good" for code).
    pick = MT.route_role(w.p, "code", scope=sid, piece="next")
    assert pick.record is not None and pick.record.name == "glm"
    assert "good" in pick.reason, pick.reason


# --------------------------------------------------------------------------- #
# 7. an older table
# --------------------------------------------------------------------------- #


def test_an_older_teamrolerun_table_gains_both_columns_and_its_rows_read_as_before(tmp_path):
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    con = sqlite3.connect(str(home / "ironjarvis.db"))
    con.execute(
        "CREATE TABLE teamrolerun (id VARCHAR PRIMARY KEY, member VARCHAR, role VARCHAR, "
        "session_id VARCHAR, scope VARCHAR, piece VARCHAR, finished BOOLEAN, check_result VARCHAR, "
        "review VARCHAR, provider VARCHAR, model VARCHAR, account VARCHAR, turn VARCHAR, "
        "created_at DATETIME)"
    )
    con.execute(
        "INSERT INTO teamrolerun VALUES ('trr_old', 'glm', 'code', 's-old', 'sc', 'p', 1, 'passed', '', "
        "'ollama', 'qwen3:8b', '', '', '2026-10-01 00:00:00')"
    )
    con.commit()
    con.close()
    # BOOT, and only boot, adds them: ``open_db`` in a CLEAN interpreter
    # imports nothing that would register the table by accident — the
    # late-model list (``core.db._LATE_MODEL_MODULES``) is what puts it in
    # the metadata the reconciler walks (the v1.151.2 lesson).
    db = home / "ironjarvis.db"
    script = "\n".join((
        "import json, sqlite3",
        "from iron_jarvis.core.db import open_db",
        f"open_db(r{str(db)!r})",
        f"con = sqlite3.connect(r{str(db)!r})",
        "cols = [r[1] for r in con.execute('PRAGMA table_info(teamrolerun)')]",
        "rows = con.execute('SELECT id, asked, files_json FROM teamrolerun').fetchall()",
        "con.close()",
        "print(json.dumps({'cols': cols, 'rows': rows}))",
    ))
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    got = json.loads([ln for ln in out.stdout.splitlines() if ln.startswith("{")][-1])
    assert {"asked", "files_json"} <= set(got["cols"]), got["cols"]
    assert got["rows"] == [["trr_old", None, None]], "an ALTER must keep the old row (NULL = before the column)"
    p = build_platform(str(tmp_path))
    (old,) = _rows(p)
    assert old.id == "trr_old" and not old.asked and TR.files_of(old) == []
    assert TR.role_record(p.engine, "glm", "code").runs == 1
    # A new row lands beside it with both columns.
    TR.note_run(p.engine, member="glm", role="code", session_id="s-new", scope="sc", finished=True,
                asked="name", files=["a.py"])
    new = next(r for r in _rows(p) if r.session_id == "s-new")
    assert new.asked == "name" and TR.files_of(new) == ["a.py"]
