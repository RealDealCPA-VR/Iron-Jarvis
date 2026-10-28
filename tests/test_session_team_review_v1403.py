"""v1.403.0 part D REVIEW — three defects in the coding-session team, each
pinned through the real code (the lanes, the router, the finish check and
the Codex adapter against its fake app-server).

1. THE LOCK WAS ESCAPED BY A ROLE THE WORKERS DO NOT TAG. A worker needs only
   the Writing code role (``harness/team.validate``) — a member made on the
   Model team page often holds just that — but the lead's brief tells it to
   ask for ``role:test`` or ``role:read``. The team router asked only members
   HOLDING the role, so a test or read piece on a code-only team came back
   "No one on this session's team holds …", which ``TeamTurn.no_worker`` read
   as "no worker could take it" and UNLOCKED the lead's coding tools: the
   lead could take its hands back every time by asking for the wrong role.
   A session's WORKERS now take every worker role's piece (the user picked
   them as workers); the unlock is left for a worker that really cannot.
2. AN UNLOCKED LEAD ON CODEX COULD NOT USE ITS NEW TOOLS
   (``tests/test_codex_tools_changed_v1403.py``).
3. ``round_gate`` WIDENED A ROUND BY ID ALONE. The finish check's test calls
   were recognised by id — any id ever handed out, in any round, beside any
   other call — so a later call carrying a reused ``ij-finish-N`` id (or a
   round mixing one with the model's own calls) got the command tool the
   lead does not hold. Only a round made ENTIRELY of the check's own
   UNSETTLED calls is widened now (``owns_review``'s rule since v1.401.0).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.harness import finish as F
from iron_jarvis.harness import team as T
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_session_team_v1403 import (  # noqa: F401
    CHANGERS,
    LOCAL,
    app_text,
    body,
    helper_sessions,
    lead_calls,
    ledger,
    members,
    piece,
    post,
    stream,
    world,
    write_x,
)

# --------------------------------------------------------------------------- #
# 1. a session's workers take every worker role's piece
# --------------------------------------------------------------------------- #


def _code_only_worker(p) -> None:
    """A worker as the Model team page makes it: the Writing code role only."""
    p.agents_registry.register("qwen", "You are qwen.", [], description="qwen codes",
                               provider=LOCAL[0], model=LOCAL[1], roles=["code"])


@pytest.mark.parametrize("role", ["test", "read"])
async def test_a_role_the_worker_does_not_tag_goes_to_the_worker_and_never_unlocks_the_lead(world, role):  # noqa: F811
    _code_only_worker(world.p)
    team = T.SessionTeam(workers=("qwen",))
    world.main = [piece("p1", f"A {role} piece for app.py.", role=role), write_x(9), "The worker did it."]
    world.worker = ["I did the piece."]
    out = await stream(world.p, body(world.ws), team=team)
    # The piece ran on the session's worker...
    (kid, *_rest) = helper_sessions(world.p)
    assert kid.agent_name == "custom:qwen"
    # ...so nothing unlocked: the lead never held a coding tool, and its
    # invented write was refused with nothing on disk.
    assert out["done"]["team"]["lead_did"] == []
    assert out["done"]["team"]["pieces"] == 1
    assert all(not set(c["tools"]) & CHANGERS for c in lead_calls(world))
    assert app_text(world.ws) == "X = 1\n"


async def test_a_worker_that_really_cannot_take_a_test_piece_still_unlocks_the_lead(world):  # noqa: F811
    """The control: the router's real "no worker could take it" (the worker's
    box is not answering) still unlocks — the fix widened WHO may take a
    piece, never when the lead may do it."""
    _code_only_worker(world.p)
    world.down.add(LOCAL[0])
    team = T.SessionTeam(workers=("qwen",))
    world.main = [piece("p1", "A test piece for app.py.", role="test"), write_x(2), "I did it myself."]
    out = await stream(world.p, body(world.ws), team=team)
    did = out["done"]["team"]["lead_did"]
    assert len(did) == 1 and "its box is not answering" in did[0]["reason"]
    assert app_text(world.ws) == "X = 2\n"


async def test_a_review_still_goes_only_to_a_member_holding_the_review_role(world):  # noqa: F811
    """The widening is for WORKER roles only: a review piece on a team whose
    reviewer lost the role is refused, never handed to a worker."""
    _code_only_worker(world.p)
    world.p.agents_registry.register("rev", "You are rev.", [], provider=LOCAL[0], model=LOCAL[1],
                                     roles=["code"])
    team = T.SessionTeam(workers=("qwen",), reviewer="rev")
    world.main = [piece("r1", "Review app.py.", role="review"), "No reviewer could take it."]
    out = await stream(world.p, body(world.ws, "review app.py"), team=team)
    assert not helper_sessions(world.p)
    assert out["done"]["team"]["lead_did"] == []


# --------------------------------------------------------------------------- #
# 3. round_gate: only a round of the check's own unsettled calls is widened
# --------------------------------------------------------------------------- #


def _check(tmp_path: Path) -> F.FinishCheck:
    turn = T.TeamTurn(team=T.SessionTeam(workers=("qwen",)))
    return F.FinishCheck(engine=None, home=tmp_path, event_bus=None, session_id="build:x", folder=tmp_path,
                         mark=0, request="r", gate={"read_file", "delegate", "shell"}, carrier="shell",
                         team=turn)


def _handed_out(check: F.FinishCheck) -> ToolCall:
    check.config = F.FinishConfig()
    check._commands.append(("shell", {"command": "python -m pytest -q"}, "", ""))
    (call,) = check._test_calls(low_trust=False)
    return call


def test_the_checks_own_pending_round_gets_its_command_tool(tmp_path):
    check = _check(tmp_path)
    call = _handed_out(check)
    assert call.id.startswith(F.CALL_ID_PREFIX)
    assert check.team_check_tools([call]) == {"shell"}


def test_a_round_mixing_the_checks_call_with_a_model_call_is_never_widened(tmp_path):
    check = _check(tmp_path)
    call = _handed_out(check)
    mine = ToolCall(id="toolu_1", name="shell", arguments={"command": "del app.py"})
    assert check.team_check_tools([call, mine]) == set()


def test_a_reused_check_id_after_the_check_settled_is_the_models_own(tmp_path):
    check = _check(tmp_path)
    call = _handed_out(check)
    check._by_id[call.id].verdict = F.TESTS_PASSED  # the check's run settled
    again = ToolCall(id=call.id, name="shell", arguments={"command": "del app.py"})
    assert check.team_check_tools([again]) == set()


def test_a_check_id_on_another_tool_is_never_widened(tmp_path):
    check = _check(tmp_path)
    call = _handed_out(check)
    other = ToolCall(id=call.id, name="write_file", arguments={"path": "app.py", "content": "x"})
    assert check.team_check_tools([other]) == set()


# --------------------------------------------------------------------------- #
# the pins the first cut left unchecked (each mutation survived the suite):
# spawn_agent's session-team rules — the door Codex's own `spawn_agent` and
# Claude Code's background `Agent` map to — the POST lane's round gate, and
# the unlock's low-trust filter
# --------------------------------------------------------------------------- #


def _spawn(cid: str, **args) -> list[ToolCall]:
    return [ToolCall(id=cid, name="spawn_agent", arguments=args)]


async def test_spawn_agent_refuses_a_helper_off_the_sessions_team(world):  # noqa: F811
    members(world.p)
    world.main = [_spawn("s1", agent="builder", task="set X"), "It could not take it."]
    out = await stream(world.p, body(world.ws))
    assert not helper_sessions(world.p)
    texts = [str(m.content) for m in lead_calls(world)[-1]["messages"] if getattr(m, "role", "") == "tool"]
    assert any("'builder' is not on this session's team" in t for t in texts), texts
    assert out["done"]["team"]["lead_did"] == []


async def test_spawn_agent_routes_a_role_piece_only_among_the_sessions_workers(world):  # noqa: F811
    # A member OFF the session's team that the whole model team's router
    # would ask first (same model, made first, first by name): never asked.
    world.p.agents_registry.register("aaa-coder", "You code.", [], provider=LOCAL[0], model=LOCAL[1],
                                     roles=["code", "test", "read"])
    members(world.p)
    world.main = [_spawn("s1", role="code", task="Set X to 2 in app.py.", piece="set X"), "Done."]
    world.worker = [write_x(2), "Changed app.py."]
    out = await stream(world.p, body(world.ws))
    (kid, *_rest) = helper_sessions(world.p)
    assert kid.agent_name == "custom:qwen"
    assert out["done"]["team"]["pieces"] == 1 and out["done"]["team"]["lead_did"] == []


async def test_spawn_agent_a_piece_no_worker_can_take_unlocks_the_lead(world):  # noqa: F811
    members(world.p)
    world.down.add(LOCAL[0])
    world.main = [_spawn("s1", role="code", task="Set X to 2 in app.py.", piece="set X"), write_x(2),
                  "No worker could take it, so I did it myself."]
    out = await stream(world.p, body(world.ws))
    calls = lead_calls(world)
    assert not set(calls[0]["tools"]) & CHANGERS
    assert "write_file" in calls[1]["tools"]
    did = out["done"]["team"]["lead_did"]
    assert len(did) == 1 and "its box is not answering" in did[0]["reason"]
    assert app_text(world.ws) == "X = 2\n"


async def test_post_lane_the_finish_checks_test_run_goes_through_the_round_gate(world, monkeypatch):  # noqa: F811
    """The POST lane's mirror of the stream lane's pin: the lead holds no
    command tool, and the check's own test run (the worker's command) is let
    through ``round_gate`` for exactly its own call."""
    members(world.p, reviewer=False)
    monkeypatch.setitem(world.p.permissions._base, "delegate", "allow")
    monkeypatch.setitem(world.p.permissions._base, "shell", "allow")
    (world.ws / "test_mini.py").write_bytes(
        b"import unittest\nimport app\n\n\nclass T(unittest.TestCase):\n"
        b"    def test_x(self):\n        self.assertEqual(app.X, 2)\n")
    world.main = [piece("p1", "Set X to 2 in app.py and run python -m unittest -q test_mini."),
                  "The worker set X and the tests pass.", "Nothing else needed changing."]
    world.worker = [write_x(2),
                    [ToolCall(id="ws1", name="shell", arguments={"command": "python -m unittest -q test_mini"})],
                    "Changed app.py; python -m unittest -q test_mini passed."]
    out = await post(world.p, body(world.ws), team=T.SessionTeam(workers=("qwen",)))
    fin = out["finish"]
    assert fin["tests"] == "passed" and fin["command"] == "python -m unittest -q test_mini"
    assert all(not set(c["tools"]) & CHANGERS for c in lead_calls(world))


def test_an_unlock_after_a_taint_never_arms_a_low_trust_tool(world):  # noqa: F811
    """A reply TAINTED after its plan was built (its full plan holds the
    process tools low trust keeps away): the unlock ARMS none of them (the
    lead's own page reads, held before the taint, are denied by the
    overrides, as in any tainted reply)."""
    from iron_jarvis.core.trust import LOW_TRUST_DENY
    from iron_jarvis.daemon.chat_turn import apply_team_unlock
    from iron_jarvis.harness import components as C
    from iron_jarvis.harness import mode as M

    members(world.p)
    p = world.p
    turn = M.build(registry=p.registry, permissions=p.permissions, requested="generic",
                   provider="google", model="gemini-3.8-flash", stream=True, layer=C.EMPTY_LAYER,
                   rule_files=[], low_trust=False, local_only=False, tool_ceiling=None, extra=[],
                   approval_mode="", plan_mode=False, ask_user=True, library=[], lead=True)
    assert turn.full_plan is not None
    held_back = set(turn.full_plan.gate) & set(LOW_TRUST_DENY)
    assert held_back, "the full plan holds no low-trust tool: the pin would be vacuous"
    turn.team = T.turn_for(p, T.SessionTeam(workers=("qwen",)), worker_tools=sorted(turn.full_plan.gate),
                           lead_provider="google", lead_model="gemini-3.8-flash")
    turn.team.no_worker("code", "No team member can take coding now: skipped qwen (busy).")
    armed, ask_armed = list(turn.plan.armed), list(turn.plan.ask)
    before = set(armed) | set(ask_armed)
    gainable = (set(turn.full_plan.gate) - before) & held_back
    assert gainable, "the lead already held every low-trust tool: the pin would be vacuous"
    overrides = {n: "allow" for n in armed}
    grants: set = set()
    specs: list = []
    assert apply_team_unlock(turn, p, armed=armed, ask_armed=ask_armed, tool_specs=specs,
                             overrides=overrides, armed_grant=grants,
                             trust_state={"low": False, "tainted": True}) is True
    assert "write_file" in armed and "write_file" in grants
    gained = (set(armed) | set(ask_armed)) - before
    assert not gained & held_back, gained & held_back
    assert all(overrides.get(n) == "deny" for n in held_back)
    assert not grants & held_back


# --------------------------------------------------------------------------- #
# THE PRODUCTION DOOR: a Build session's turn (``routes/build.
# start_build_turn``) arms the session's team, and its reply line keeps the
# record — every pin above armed the team by hand, so a start_build_turn that
# armed none (and a reply line that dropped `team`) left the suite green.
# --------------------------------------------------------------------------- #


@pytest.fixture()
def app(tmp_path):
    from iron_jarvis.daemon.app import create_app

    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def folder(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "app.py").write_bytes(b"x = 1\n")
    return work


#: Claude Code's names (the session's model is Claude) for every real tool
#: that changes a file or runs something.
_CLAUDE_CHANGERS = {"Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "KillShell", "TaskStop"}


async def test_a_build_sessions_turn_runs_its_model_as_the_locked_lead_and_keeps_the_record(app, folder):
    from tests.test_build_sessions_v1367 import Script, messages_of, new_session, open_turn, wire
    from tests.test_build_sessions_v1367 import frames_of as build_frames

    app.state.platform.agents_registry.register("qwen", "You are qwen.", [], provider=LOCAL[0],
                                                model=LOCAL[1], roles=["code"])
    sid = new_session(app, folder, team={"workers": ["qwen"], "reviewer": ""})["id"]
    script = wire(app, Script(["I read the project; nothing to hand out yet."]))
    status, raw = await open_turn(app, sid, {"text": "what does app.py do?", "turn_id": "tt-1"})
    assert status == 200, raw
    frames = build_frames(raw)
    done = next(d for e, d in frames if e == "done")
    first = script.calls[0]
    assert "Agent" in first["tools"], first["tools"]
    assert not set(first["tools"]) & (_CLAUDE_CHANGERS | CHANGERS), first["tools"]
    assert T.TEAM_BRIEF_HEADER in first["system"]
    assert list(done)[-1] == "team" and done["team"]["workers"] == [LOCAL[1]]
    # The Build reply line keeps the record (harness/sessions.LINE_KEYS).
    reply = [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]
    assert reply.get("team") == T.saved_record(done["team"])


async def test_a_solo_build_sessions_turn_is_not_a_team_turn(app, folder):
    """The control: no team on the session — the full coding set, no brief,
    no record."""
    from tests.test_build_sessions_v1367 import Script, messages_of, new_session, open_turn, wire
    from tests.test_build_sessions_v1367 import frames_of as build_frames

    sid = new_session(app, folder)["id"]
    script = wire(app, Script(["I read it."]))
    status, raw = await open_turn(app, sid, {"text": "what does app.py do?", "turn_id": "tt-2"})
    assert status == 200, raw
    done = next(d for e, d in build_frames(raw) if e == "done")
    assert "Write" in script.calls[0]["tools"] and T.TEAM_BRIEF_HEADER not in script.calls[0]["system"]
    assert "team" not in done
    assert "team" not in [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]


def test_a_paused_worker_reads_unavailable_beside_a_ready_one(app, folder, monkeypatch):
    """The routes pin read both workers' provider as not connected, so it
    held whatever ``member_state`` did with a pause: here the provider
    answers, and only the paused worker reads unavailable."""
    from fastapi.testclient import TestClient

    p = app.state.platform
    real = p.providers.available
    monkeypatch.setattr(p.providers, "available", lambda name: True if name == LOCAL[0] else real(name))
    p.agents_registry.register("qwen", "q", [], provider=LOCAL[0], model=LOCAL[1], roles=["code"])
    p.agents_registry.register("glm", "g", [], provider=LOCAL[0], model="glm", roles=["code"])
    p.agents_registry.set_paused("glm", "a day off")
    client = TestClient(app)
    r = client.post("/build/sessions", json={"folder": str(folder), "team": {"workers": ["qwen", "glm"]}})
    assert r.status_code == 201, r.text
    view = client.get(f"/build/sessions/{r.json()['id']}").json()
    states = {m["name"]: m["state"] for m in view["team"]["workers"]}
    assert states == {"qwen": "ready", "glm": "unavailable"}


# --------------------------------------------------------------------------- #
# the second review's own conditions (each guard survived its deletion)
# --------------------------------------------------------------------------- #


async def test_no_second_review_when_no_worker_changed_anything_after_the_first(world):  # noqa: F811
    members(world.p)
    world.main = [piece("p1", "Set X to 2 in app.py."), "The worker set X to 2.",
                  "The reviewer is wrong: nothing needs changing."]
    world.worker = [write_x(2), "Changed app.py."]
    world.reviewer = ["Rename X.\nVERDICT: changes needed", "Never asked.\nVERDICT: approved"]
    out = await stream(world.p, body(world.ws))
    rows = [r for r in ledger(world.p) if r.called_as == F.REVIEW_CALLED_AS]
    assert len(rows) == 1 and len(world.reviewer) == 1
    assert out["done"]["finish"]["review"]["rounds"] == 1


async def test_no_second_review_in_low_trust(tmp_path, monkeypatch):
    """A reply that turned low trust after its first review hands nothing more
    to a reviewer (the first review's own rule) — with a fix on disk that
    would otherwise be reviewed (the control below)."""
    fixed = F.TurnFiles(changed=[tmp_path / "app.py"])
    monkeypatch.setattr(F, "read_turn_files", lambda *a, **k: fixed)
    monkeypatch.setattr(F.FinishCheck, "_second_brief", lambda self, files: "the fix")

    def _after_a_first_review() -> F.FinishCheck:
        check = _check(tmp_path)
        check.review_rounds = 1
        check._review_wanted = True
        check.review = {"state": F.REVIEW_DONE, "verdict": F.VERDICT_CHANGES, "member": "rev", "model": ""}
        return check

    low = _after_a_first_review()
    assert await low._second_review(reply="x", round_index=0, rounds=40, truncated=False,
                                    low_trust=True) is None
    assert low.review_rounds == 1 and not low._second_note_due
    # The control: the same reply in full trust is reviewed once more.
    full = _after_a_first_review()
    calls = await full._second_review(reply="x", round_index=0, rounds=40, truncated=False, low_trust=False)
    assert calls and calls[0].id == F.REVIEW_CALL_ID_2 and full.review_rounds == 2
