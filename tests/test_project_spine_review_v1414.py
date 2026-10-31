"""Independent review of v1.414.0 (the context spine): defects found, each
pinned red-first.

1. A LOCAL-ONLY agent run's TASK and SUMMARY reached a turn that leaves this
   network through "Recent activity in this project". v1.414.0 withheld a
   local-only chat's or coding session's TITLE — but the run lines (task
   text + summary, far more than a title) of a session switched to local only
   (``options_json.local_only``) still rode every project turn and run. The
   one helper that now owns the rule (``recent_activity.recent_lines``)
   withholds them too, by the same fabric-recall rule.
2. Retry of a failed coding-session turn minted a preference from ONE
   correction: Retry sends the failed question again as a new turn, so the
   body held the same words twice ([Q, (failed reply, no words), Q]) and the
   look-back counted the first copy as an earlier saying.
3. The widened one-off filter rejected corrections that SAY they are lasting
   ("you keep using tables, even here", "no emoji anywhere, including here",
   "today and every day") and "in our chat(s)" — chats with Jarvis are not one
   place in the code.
4. A coding session parked on a QUESTION (not only a card) read "waiting for
   your OK on a card".
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatThreadRecord, Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.learning import preferences as prefs
from iron_jarvis.learning.corrections import is_correction
from iron_jarvis.projects import recent_activity as RA
from iron_jarvis.projects import world as W
from iron_jarvis.providers import egress
from tests.test_build_sessions_v1367 import Script, frames_of, new_session, open_turn, wire
from tests.test_project_recent_activity_v1414 import _block, _both_lanes, _runtime_block


@pytest.fixture()
def scene(tmp_path):
    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    root = tmp_path / "acme"
    (root / "src").mkdir(parents=True)
    r = client.post("/projects", json={"name": "Acme", "root": str(root)})
    assert r.status_code == 200, r.text

    class Scene:
        pass

    s = Scene()
    s.app, s.client, s.platform, s.pid, s.root = app, client, app.state.platform, r.json()["id"], root
    return s


def _run(scene, task, *, local_only=False, hours_ago=5.0) -> Session:
    with session_scope(scene.platform.engine) as db:
        s = Session(
            task=task, summary=f"summary of {task}", project_id=scene.pid,
            status=SessionStatus.COMPLETED,
            created_at=utcnow() - timedelta(hours=hours_ago),
            options_json=json.dumps({"local_only": True}) if local_only else None,
        )
        db.add(s)
        db.commit()
        db.refresh(s)
        db.expunge(s)
    return s


# --------------------------------------------------------------------------- #
# 1. a local-only RUN's task never reaches a turn that leaves this network
# --------------------------------------------------------------------------- #


def test_a_local_only_runs_task_and_summary_stay_home_in_both_lanes_and_the_runtime(scene):
    _run(scene, "SECRET-LOCAL-RUN prepare the 1040", local_only=True, hours_ago=1)
    _run(scene, "Shared run", hours_ago=2)
    for system in _both_lanes(scene):
        assert "SECRET-LOCAL-RUN" not in system
        assert any("Shared run" in ln for ln in _block(system))
    runtime = "\n".join(_runtime_block(scene))
    assert "SECRET-LOCAL-RUN" not in runtime and "Shared run" in runtime
    # Work that STAYS on this network still sees it (the fabric recall's rule).
    for system in _both_lanes(scene, local_only=True):
        assert "SECRET-LOCAL-RUN" in "\n".join(_block(system))
    # …but not a turn the user allowed off the network for this message.
    runs = [_run(scene, "SECRET-ONCE local", local_only=True, hours_ago=0.5)]
    with egress.local_only_scope(True, allowed_once="anthropic"):
        assert RA.recent_lines(scene.platform.engine, scene.pid, runs) == []


def test_runs_that_are_not_local_only_keep_the_exact_old_lines(scene):
    runs = [_run(scene, "Plain run A", hours_ago=1), _run(scene, "Plain run B", hours_ago=2)]
    old = [f"- [{s.status.value}] {s.task[:80]}: {(s.summary or '(no summary)')[:160]}" for s in runs]
    assert RA.recent_lines(scene.platform.engine, scene.pid, runs) == old


# --------------------------------------------------------------------------- #
# 2. Retry of a failed coding-session turn is not a second saying
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _a_generous_lookback_budget(monkeypatch):
    monkeypatch.setattr(prefs, "TURN_BUDGET_S", 60.0)


def _done(frames) -> dict:
    return [d for e, d in frames if e == "done"][-1]


async def _turn(app, sid, text, turn_id):
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id})
    assert status == 200, raw
    return _done(frames_of(raw))


def _stored(app, sid, lines):
    with session_scope(app.state.platform.engine) as db:
        rec = db.get(ChatThreadRecord, sid)
        rec.messages_json = json.dumps(lines)
        db.add(rec)
        db.commit()


@pytest.mark.parametrize("failed", [
    {"role": "assistant", "content": "", "error": "Claude Code couldn't answer.", "turnId": "t0"},
    {"role": "assistant", "content": "Here is a tab", "interrupted": True, "turnId": "t0"},
])
async def test_retrying_a_failed_correction_never_mints_from_one_saying(scene, tmp_path, failed):
    sid = new_session(scene.app, scene.root / "src")["id"]
    wire(scene.app, Script([]))
    _stored(scene.app, sid, [
        {"role": "user", "content": "stop using tables", "turnId": "t0"},
        failed,
    ])
    done = await _turn(scene.app, sid, "stop using tables", "retry-1")
    assert done.get("suggestion") is None, done


async def test_saying_it_again_after_a_real_reply_still_counts(scene):
    sid = new_session(scene.app, scene.root / "src")["id"]
    wire(scene.app, Script([]))
    _stored(scene.app, sid, [
        {"role": "user", "content": "stop using tables", "turnId": "t0"},
        {"role": "assistant", "content": "Sure, here it is | a | b |", "turnId": "t0"},
    ])
    done = await _turn(scene.app, sid, "stop using tables", "again-1")
    assert done.get("suggestion") is not None and done["suggestion"]["count"] == 2, done


# --------------------------------------------------------------------------- #
# 3. a correction that says it is lasting is not a one-off
# --------------------------------------------------------------------------- #

LASTING_STILL_CORRECTIONS = [
    "you keep using tables, even here",
    "no emoji anywhere, including here",
    "stop apologizing, I mean it, today and every day",
    "stop apologizing, right now and forever",
    "stop using emoji in our chat",
    "keep it short in our conversation",
    "you always apologize, even right now",
]

STILL_ONE_OFF = [
    "be brief right now",
    "too long today",
    "too long here",
    "no preamble in this session",
    "keep it short in this conversation",
    "too verbose in our repo",
    "stop asking me first on our app",
    "stop apologizing in this chat",
]


@pytest.mark.parametrize("text", LASTING_STILL_CORRECTIONS)
def test_a_correction_that_says_it_lasts_is_still_one(text):
    assert is_correction(text) is True, text


@pytest.mark.parametrize("text", STILL_ONE_OFF)
def test_a_scoped_correction_is_still_not_one(text):
    assert is_correction(text) is False, text


# --------------------------------------------------------------------------- #
# 4. waiting words fit a question as well as a card
# --------------------------------------------------------------------------- #


def test_a_waiting_coding_session_reads_true_for_a_question_too():
    snap = {"active": [], "assignments": [], "attention": [], "reviews": []}
    coding = [{
        "kind": "coding_session", "view": "chat_code", "id": "c1", "key": "coding:chat_code:c1",
        "title": "fix the build", "tool": "", "state": "waiting", "link": "/chat?view=code&session=c1",
        "_updated": utcnow(),
    }]
    item = W.waiting_items("p1", snap, {}, coding)[0]
    assert "card" not in item["reason"] and "OK" not in item["reason"], item["reason"]


def test_the_runtimes_fallback_never_hands_on_a_local_only_run(scene, monkeypatch):
    _run(scene, "SECRET-FALLBACK local", local_only=True, hours_ago=1)
    _run(scene, "Shared fallback run", hours_ago=2)

    def boom(*a, **k):
        raise RuntimeError("the merge failed")

    monkeypatch.setattr(RA, "recent_lines", boom)
    block = "\n".join(_runtime_block(scene))
    assert "SECRET-FALLBACK" not in block and "Shared fallback run" in block


# --------------------------------------------------------------------------- #
# the ONE locate rule (a pin the commit did not have)
# --------------------------------------------------------------------------- #


def test_best_project_matches_on_a_folder_boundary_case_folded_and_skips_archived():
    from types import SimpleNamespace as P

    from iron_jarvis.projects.locate import best_project

    a = P(id="a", root=r"C:\work\proj-a", status="active")
    a2 = P(id="a2", root=r"C:\Work\Proj-A2\\", status="active")
    deep = P(id="deep", root=r"c:/work/proj-a/src", status="archived")
    projects = [a, a2, deep]
    assert best_project(projects, r"C:\work\proj-a2\src").id == "a2"   # never proj-a
    assert best_project(projects, r"c:/WORK/proj-a/src/x.py").id == "a"  # archived deeper root skipped
    assert best_project(projects, r"C:\work\proj-a").id == "a"
    assert best_project(projects, r"C:\work\proj-ab") is None
    assert best_project(projects, "") is None


async def test_a_different_correction_after_a_failed_reply_still_counts(scene):
    """Only the SAME words resent are a retry: a new way of saying it after a
    failed reply is a second saying."""
    sid = new_session(scene.app, scene.root / "src")["id"]
    wire(scene.app, Script([]))
    _stored(scene.app, sid, [
        {"role": "user", "content": "stop using tables", "turnId": "t0"},
        {"role": "assistant", "content": "", "error": "Claude Code couldn't answer.", "turnId": "t0"},
    ])
    done = await _turn(scene.app, sid, "no tables please", "new-words-1")
    assert done.get("suggestion") is not None and done["suggestion"]["count"] == 2, done


# --------------------------------------------------------------------------- #
# 5. a Build › Code session on Pi or OpenCode may run on a local model: its
#    title stays home like a local-only one (Claude Code / Codex keep today's
#    behaviour — their words already went to that vendor)
# --------------------------------------------------------------------------- #


def _hosted(scene, harness, title, hours_ago):
    from iron_jarvis.hosted import store as hosted_store

    row = hosted_store.create(scene.platform.engine, harness=harness, folder=str(scene.root / "src"),
                              title=title)
    hosted_store.update(scene.platform.engine, row.id, updated_at=utcnow() - timedelta(hours=hours_ago))
    return row.id


def test_a_pi_or_opencode_sessions_title_stays_home_but_claude_code_is_listed(scene):
    _run(scene, "A run", hours_ago=9)
    _hosted(scene, "pi", "SECRET-PI-TITLE", 1)
    _hosted(scene, "opencode", "SECRET-OPENCODE-TITLE", 2)
    _hosted(scene, "claude-code", "Claude Code work", 3)
    _hosted(scene, "codex", "Codex work", 4)
    for system in _both_lanes(scene):
        block = "\n".join(_block(system))
        assert "SECRET-PI-TITLE" not in system and "SECRET-OPENCODE-TITLE" not in system
        assert "Claude Code work" in block and "Codex work" in block
    runtime = "\n".join(_runtime_block(scene))
    assert "SECRET-PI" not in runtime and "SECRET-OPENCODE" not in runtime
    assert "Claude Code work" in runtime and "Codex work" in runtime
    # Work that stays on this network still sees them.
    for system in _both_lanes(scene, local_only=True):
        block = "\n".join(_block(system))
        assert "SECRET-PI-TITLE" in block and "SECRET-OPENCODE-TITLE" in block
    # The project page still lists every one (it is the user's own screen),
    # and the wire says nothing new.
    rows = scene.client.get(f"/projects/{scene.pid}").json()["coding_sessions"]
    assert {r["title"] for r in rows} >= {"SECRET-PI-TITLE", "SECRET-OPENCODE-TITLE", "Claude Code work"}
    assert all(r["local_only"] is False for r in rows if r["view"] == "build_code")
